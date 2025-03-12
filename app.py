from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash
import os
import time
import json
import pdfplumber
import requests
import zipfile
import shutil
import csv
from email_invoice_downloader import connect_to_email, download_invoice_attachments
from dotenv import load_dotenv
from datetime import datetime
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
# 修改导入语句，适应新版本的Werkzeug
try:
    from werkzeug.urls import url_parse
except ImportError:
    # 新版本的Werkzeug中，url_parse可能在其他模块中
    try:
        from werkzeug.utils import url_parse
    except ImportError:
        # 如果仍然找不到，使用urllib.parse作为备选
        from urllib.parse import urlparse as url_parse
from models import db, User, EmailAccount, InvoiceHistory, Invoice
from forms import LoginForm, RegistrationForm, EmailAccountForm, InvoiceDownloadForm

# 加载环境变量
load_dotenv(override=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-please-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化数据库
db.init_app(app)

# 初始化登录管理器
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录以访问此页面。'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 配置信息
class Config:
    @classmethod
    def _ensure_env_loaded(cls):
        """确保环境变量已加载"""
        load_dotenv(override=True)

    @classmethod
    def get_model(cls):
        cls._ensure_env_loaded()
        model = os.getenv('OPENAI_MODEL')
        #print(f"从环境变量读取的模型: {model}")  # 调试信息
        return model or 'gpt-4o'  # 只有当环境变量不存在时才使用默认值
    
    @classmethod
    def get_api_base(cls):
        cls._ensure_env_loaded()
        return os.getenv('OPENAI_API_BASE') or 'http://10.10.10.16:3000'
    
    @classmethod
    def get_api_key(cls):
        cls._ensure_env_loaded()
        return os.getenv('OPENAI_API_KEY', '')
    
    @classmethod
    def get_port(cls):
        cls._ensure_env_loaded()
        return int(os.getenv('APP_PORT') or 5001)
    
    @classmethod
    def get_host(cls):
        cls._ensure_env_loaded()
        return os.getenv('APP_HOST') or '0.0.0.0'

# 全局变量，用于跟踪处理进度
processing_status = {
    'status': 'idle',  # 状态：idle, processing, complete, error
    'current': 0,      # 当前处理的文件索引
    'total': 0,        # 总文件数
    'current_file': '',  # 当前处理的文件名
    'redirect_url': '',  # 处理完成后的重定向URL
    'error': ''        # 错误信息
}

def extract_invoice_info(pdf_path):
    """使用自定义 OpenAI 代理服务器从PDF发票中提取信息"""
    requested_model = Config.get_model()
    #print(f"请求使用的模型: {requested_model}")  # 打印请求的模型
    
    try:
        # 读取 PDF 文本
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()
        
        # 构建 prompt
        prompt = f"""
        请从以下发票文本中提取关键信息，以JSON格式返回以下字段：
        - invoice_date (开票日期)
        - seller (开票方名称)
        - amount (含税金额，只需数字)
        - project_name (项目名称，从货物或应税劳务、服务名称中提取)
        - invoice_no (发票号码)

        发票文本内容：
        {text}

        请只返回JSON格式的数据，不要有其他说明文字。格式如下：
        {{
            "invoice_date": "YYYY年MM月DD日",
            "seller": "公司名称",
            "amount": "金额数字",
            "project_name": "项目名称",
            "invoice_no": "发票号码"
        }}
        """

        # 调用自定义 OpenAI 代理服务器
        response = requests.post(
            f"{Config.get_api_base()}/v1/chat/completions",
            json={
                "model": Config.get_model(),
                "messages": [
                    {"role": "system", "content": "你是一个专门处理发票信息的助手，请严格按照要求的JSON格式返回提取的信息。如果是机票或者火车票，请务必把【出发地-目的地，出发日期，出发时间，航班号/车次，舱位等级】填入项目名称。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0
            },
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {Config.get_api_key()}'
            }
        )
        
        # 打印原始响应以进行调试
        print("API Response:", response.text)
        result = response.json()
        actual_model = result.get('model', 'unknown')  # 获取实际使用的模型
        #print(f"实际使用的模型: {actual_model}")  # 打印实际使用的模型
        
        # 检查响应结构
        if 'response' in result:
            content = result['response']
        elif 'choices' in result and len(result['choices']) > 0:
            content = result['choices'][0]['message']['content']
        else:
            print(f"未知的响应格式: {result}")
            return None
        
        # 清理 Markdown 格式
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:]  # 移除 ```json
        if content.endswith('```'):
            content = content[:-3]  # 移除结尾的 ```
        content = content.strip()
        
        # 解析内容中的 JSON
        extracted_info = json.loads(content)
        # 添加文件名
        extracted_info['filename'] = os.path.basename(pdf_path)
        # 添加原始文件路径
        extracted_info['filepath'] = pdf_path
        return extracted_info
            
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        print(f"尝试解析的内容: {content}")
        return None
    except Exception as e:
        print(f"API调用错误: {e}")
        print(f"完整响应: {result}")
        return None

def create_invoice_csv(invoice_info_list, output_dir):
    """创建发票信息的CSV文件"""
    csv_path = os.path.join(output_dir, "发票信息汇总.csv")
    
    # 定义CSV表头
    fieldnames = ['发票号码', '开票日期', '开票方名称', '含税金额', '项目名称', '原文件名', '重命名后文件名']
    
    # 写入CSV文件
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for info in invoice_info_list:
            # 获取原始文件名
            original_filename = info.get('filename', '')
            
            # 构建重命名后的文件名
            invoice_date = info.get('invoice_date', '未知日期').replace('/', '-').replace(' ', '')
            seller = info.get('seller', '未知开票方').replace('/', '_')
            # 单独处理反斜杠
            seller = seller.replace('\\', '_')
            if len(seller) > 20:
                seller = seller[:20]
            amount = info.get('amount', '未知金额')
            invoice_no = info.get('invoice_no', '未知发票号')
            
            new_filename = f"[{invoice_date}-{seller}-{amount}-{invoice_no}].pdf"
            new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            
            writer.writerow({
                '发票号码': info.get('invoice_no', ''),
                '开票日期': info.get('invoice_date', ''),
                '开票方名称': info.get('seller', ''),
                '含税金额': info.get('amount', ''),
                '项目名称': info.get('project_name', ''),
                '原文件名': original_filename,
                '重命名后文件名': new_filename
            })
    
    return csv_path

def rename_invoice_files(invoice_info_list):
    """根据提取的信息重命名发票文件"""
    renamed_files = []
    renamed_dir = 'renamed_invoices'
    
    # 确保目录存在
    if os.path.exists(renamed_dir):
        shutil.rmtree(renamed_dir)  # 清空目录
    os.makedirs(renamed_dir)
    
    for info in invoice_info_list:
        try:
            # 获取原始文件路径
            original_path = info['filepath']
            
            # 清理文件名中的非法字符
            invoice_date = info.get('invoice_date', '未知日期').replace('/', '-').replace(' ', '')
            seller = info.get('seller', '未知开票方').replace('/', '_')
            # 单独处理反斜杠
            seller = seller.replace('\\', '_')
            amount = info.get('amount', '未知金额')
            invoice_no = info.get('invoice_no', '未知发票号')
            
            # 限制文件名长度
            if len(seller) > 20:
                seller = seller[:20]
            
            # 构建新文件名
            new_filename = f"[{invoice_date}-{seller}-{amount}-{invoice_no}].pdf"
            # 替换文件名中的非法字符
            new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            
            # 新文件路径
            new_path = os.path.join(renamed_dir, new_filename)
            
            # 复制并重命名文件
            shutil.copy2(original_path, new_path)
            renamed_files.append(new_path)
            
            print(f"重命名文件: {os.path.basename(original_path)} -> {new_filename}")
        except Exception as e:
            print(f"重命名文件时出错: {e}")
    
    # 创建CSV文件
    csv_path = create_invoice_csv(invoice_info_list, renamed_dir)
    renamed_files.append(csv_path)
    
    return renamed_dir, renamed_files

def create_invoice_zip(renamed_dir):
    """创建发票文件的ZIP压缩包"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_filename = f"invoices_{timestamp}.zip"
    
    # 为每个用户创建单独的目录
    user_static_dir = os.path.join('static', f'user_{current_user.id}')
    os.makedirs(user_static_dir, exist_ok=True)
    
    zip_path = os.path.join(user_static_dir, zip_filename)
    
    # 创建ZIP文件
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(renamed_dir):
            for file in files:
                file_path = os.path.join(root, file)
                # 将文件添加到ZIP中，使用相对路径
                zipf.write(file_path, os.path.basename(file_path))
    
    return zip_filename

def save_invoice_to_db(invoice_info, history_id=None):
    """将发票信息保存到数据库"""
    # 构建重命名后的文件名
    invoice_date = invoice_info.get('invoice_date', '未知日期').replace('/', '-').replace(' ', '')
    seller = invoice_info.get('seller', '未知开票方').replace('/', '_')
    # 单独处理反斜杠
    seller = seller.replace('\\', '_')
    if len(seller) > 20:
        seller = seller[:20]
    amount = invoice_info.get('amount', '未知金额')
    invoice_no = invoice_info.get('invoice_no', '未知发票号')
    
    new_filename = f"[{invoice_date}-{seller}-{amount}-{invoice_no}].pdf"
    new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
    
    # 创建发票记录
    invoice = Invoice(
        user_id=current_user.id,
        history_id=history_id,
        invoice_no=invoice_info.get('invoice_no', ''),
        invoice_date=invoice_info.get('invoice_date', ''),
        seller=invoice_info.get('seller', ''),
        amount=invoice_info.get('amount', ''),
        project_name=invoice_info.get('project_name', ''),
        original_filename=invoice_info.get('filename', ''),
        current_filename=new_filename,
        file_path=invoice_info.get('filepath', '')
    )
    
    db.session.add(invoice)
    db.session.commit()
    return invoice

@app.route('/')
def index():
    """首页"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """用户登录"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash('用户名或密码错误')
            return redirect(url_for('login'))
        
        login_user(user, remember=form.remember_me.data)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        next_page = request.args.get('next')
        if not next_page or url_parse(next_page).netloc != '':
            next_page = url_for('dashboard')
        return redirect(next_page)
    
    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    """用户登出"""
    logout_user()
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """用户注册"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录')
        return redirect(url_for('login'))
    
    return render_template('register.html', form=form)

@app.route('/dashboard')
@login_required
def dashboard():
    """用户仪表盘"""
    email_accounts = EmailAccount.query.filter_by(user_id=current_user.id).all()
    histories = InvoiceHistory.query.filter_by(user_id=current_user.id).order_by(InvoiceHistory.processed_at.desc()).limit(10).all()
    return render_template('dashboard.html', email_accounts=email_accounts, histories=histories)

@app.route('/process_status')
@login_required
def process_status():
    """获取处理进度"""
    return jsonify(processing_status)

@app.route('/email_accounts', methods=['GET', 'POST'])
@login_required
def email_accounts():
    """管理邮箱账号"""
    form = EmailAccountForm()
    if form.validate_on_submit():
        account = EmailAccount(
            user_id=current_user.id,
            email_address=form.email_address.data,
            password=form.password.data,  # 实际应用中应加密存储
            description=form.description.data
        )
        db.session.add(account)
        db.session.commit()
        flash('邮箱账号已保存')
        return redirect(url_for('email_accounts'))
    
    accounts = EmailAccount.query.filter_by(user_id=current_user.id).all()
    return render_template('email_accounts.html', form=form, accounts=accounts)

@app.route('/delete_email_account/<int:id>')
@login_required
def delete_email_account(id):
    """删除邮箱账号"""
    account = EmailAccount.query.get_or_404(id)
    if account.user_id != current_user.id:
        flash('无权限删除此账号')
        return redirect(url_for('email_accounts'))
    
    db.session.delete(account)
    db.session.commit()
    flash('邮箱账号已删除')
    return redirect(url_for('email_accounts'))

@app.route('/download_invoices', methods=['GET', 'POST'])
@login_required
def download_invoices():
    """导入发票页面"""
    form = InvoiceDownloadForm()
    
    # 获取用户保存的邮箱账号
    email_accounts = EmailAccount.query.filter_by(user_id=current_user.id).all()
    
    if form.validate_on_submit():
        # 重定向到处理页面
        return redirect(url_for('show_processing', 
                               email=form.email_account.data, 
                               password=form.password.data,
                               search_date=form.search_date.data.strftime('%Y-%m-%d') if form.search_date.data else ''))
    
    return render_template('download_invoices.html', form=form, email_accounts=email_accounts)

@app.route('/show_processing')
@login_required
def show_processing():
    """显示处理进度页面"""
    # 重置处理状态
    global processing_status
    processing_status = {
        'status': 'idle',
        'current': 0,
        'total': 0,
        'current_file': '',
        'redirect_url': '',
        'error': ''
    }
    
    # 获取参数
    email = request.args.get('email')
    password = request.args.get('password')
    search_date = request.args.get('search_date')
    
    if not email or not password:
        flash('请输入邮箱和密码')
        return redirect(url_for('download_invoices'))
    
    # 启动后台处理线程
    import threading
    thread = threading.Thread(target=process_invoices_thread, args=(email, password, search_date))
    thread.daemon = True
    thread.start()
    
    return render_template('processing.html')

def process_invoices_thread(email, password, search_date):
    """后台线程处理发票"""
    global processing_status
    
    try:
        processing_status['status'] = 'processing'
        
        # 连接邮箱
        processing_status['current_file'] = '正在连接邮箱...'
        imap = connect_to_email(email, password)
        if not imap:
            processing_status['status'] = 'error'
            processing_status['error'] = '邮箱连接失败，请检查账号和密码是否正确'
            return
        
        # 处理日期参数
        date_since = None
        if search_date:
            try:
                date_since = datetime.strptime(search_date, '%Y-%m-%d')
            except ValueError:
                processing_status['status'] = 'error'
                processing_status['error'] = '日期格式无效，请使用YYYY-MM-DD格式'
                return
        
        # 下载附件
        processing_status['current_file'] = '正在下载邮件附件...'
        start_time = time.time()
        downloaded_count = download_invoice_attachments(imap, date_since=date_since)
        imap.logout()
        
        # 获取下载的文件列表并提取信息
        downloads_dir = 'downloads'
        invoice_info = []
        duplicate_invoices = []  # 存储重复的发票信息
        new_invoices = []  # 存储新的发票信息
        
        if os.path.exists(downloads_dir):
            files = [f for f in os.listdir(downloads_dir) if f.lower().endswith('.pdf')]
            processing_status['total'] = len(files)
            
            for i, file in enumerate(files):
                processing_status['current'] = i + 1
                file_path = os.path.join(downloads_dir, file)
                processing_status['current_file'] = file
                
                info = extract_invoice_info(file_path)
                if info:
                    invoice_info.append(info)
            
            # 创建处理历史
            history = InvoiceHistory(
                user_id=current_user.id,
                search_date=date_since.date() if date_since else None,
                invoice_count=0,  # 先设为0，后面再更新
                processed_at=datetime.utcnow()
            )
            
            # 查找是否使用了保存的邮箱账号
            email_account = EmailAccount.query.filter_by(user_id=current_user.id, email_address=email).first()
            if email_account:
                history.email_account_id = email_account.id
            
            with app.app_context():
                db.session.add(history)
                db.session.commit()
            
            # 检查每张发票是否已存在
            processing_status['current_file'] = '正在检查重复发票...'
            for info in invoice_info:
                with app.app_context():
                    if check_duplicate_invoice(info):
                        # 发票已存在，添加到重复列表
                        duplicate_invoices.append(info)
                    else:
                        # 新发票，添加到新发票列表
                        new_invoices.append(info)
            
            # 只处理新发票
            if new_invoices:
                processing_status['current_file'] = '正在重命名文件...'
                # 重命名文件并创建CSV
                renamed_dir, renamed_files = rename_invoice_files(new_invoices)
                
                processing_status['current_file'] = '正在创建ZIP文件...'
                # 创建ZIP文件
                zip_filename = create_invoice_zip(renamed_dir)
                
                # 更新处理历史
                with app.app_context():
                    history.invoice_count = len(new_invoices)
                    history.zip_filename = zip_filename
                    db.session.commit()
                
                # 保存每张新发票到数据库
                processing_status['current_file'] = '正在保存发票信息到数据库...'
                for info in new_invoices:
                    with app.app_context():
                        save_invoice_to_db(info, history.id)
            else:
                # 没有新发票，更新处理历史
                with app.app_context():
                    history.invoice_count = 0
                    db.session.commit()
            
            # 计算处理时间
            processing_time = time.time() - start_time
            
            # 构建提示信息
            date_message = f"，检索{search_date}之后的邮件" if search_date else ""
            duplicate_message = f"，其中 {len(duplicate_invoices)} 张为重复发票" if duplicate_invoices else ""
            
            with app.app_context():
                flash(f'''成功下载并处理 {len(files)} 个文件{date_message}
发现 {len(invoice_info)} 张发票，成功导入 {len(new_invoices)} 张新发票{duplicate_message}
处理时间: {processing_time:.2f} 秒
本次下载: {downloaded_count} 个PDF附件
本次处理使用的大模型：{Config.get_model()}''')
            
            # 设置处理完成状态
            processing_status['status'] = 'complete'
            
            # 如果有新发票，跳转到结果页面
            if new_invoices:
                processing_status['redirect_url'] = url_for('invoice_results', 
                                                          new_count=len(new_invoices),
                                                          dup_count=len(duplicate_invoices),
                                                          zip_file=f"/static/user_{current_user.id}/{zip_filename}")
            else:
                # 没有新发票，跳转到下载页面
                if duplicate_invoices:
                    processing_status['redirect_url'] = url_for('download_invoices')
                else:
                    processing_status['redirect_url'] = url_for('download_invoices')
        else:
            processing_status['status'] = 'error'
            processing_status['error'] = '没有找到发票附件'
            
    except Exception as e:
        processing_status['status'] = 'error'
        processing_status['error'] = str(e)
        print(f'处理过程中出现错误: {str(e)}')

@app.route('/invoice_results')
@login_required
def invoice_results():
    """显示发票处理结果"""
    new_count = request.args.get('new_count', 0, type=int)
    dup_count = request.args.get('dup_count', 0, type=int)
    zip_file = request.args.get('zip_file', '')
    
    # 获取新导入的发票
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).limit(new_count).all()
    
    # 获取重复的发票信息（这里只能显示基本信息，因为详细信息已经在处理过程中丢失）
    duplicate_info = []
    
    return render_template('invoice_results.html', 
                          invoice_info=invoices,
                          duplicate_invoices=duplicate_info,
                          zip_file=zip_file)

@app.route('/process_invoices')
@login_required
def process_invoices():
    """处理发票下载请求（旧版本，保留以兼容）"""
    return redirect(url_for('download_invoices'))

@app.route('/history')
@login_required
def history():
    """查看历史记录"""
    histories = InvoiceHistory.query.filter_by(user_id=current_user.id).order_by(InvoiceHistory.processed_at.desc()).all()
    return render_template('history.html', histories=histories)

@app.route('/static/user_<int:user_id>/<path:filename>')
@login_required
def download_file(user_id, filename):
    """下载文件"""
    if user_id != current_user.id:
        flash('无权限下载此文件')
        return redirect(url_for('dashboard'))
    
    return send_file(os.path.join('static', f'user_{user_id}', filename), as_attachment=True)

# 新增发票管理相关路由
@app.route('/invoices')
@login_required
def invoices():
    """发票管理页面"""
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # 获取筛选参数
    seller = request.args.get('seller', '')
    invoice_no = request.args.get('invoice_no', '')
    invoice_date_from = request.args.get('date_from', '')
    invoice_date_to = request.args.get('date_to', '')
    amount_from = request.args.get('amount_from', '')
    amount_to = request.args.get('amount_to', '')
    
    # 构建查询
    query = Invoice.query.filter_by(user_id=current_user.id)
    
    # 应用筛选条件
    if seller:
        query = query.filter(Invoice.seller.like(f'%{seller}%'))
    if invoice_no:
        query = query.filter(Invoice.invoice_no.like(f'%{invoice_no}%'))
    
    # 分页获取发票
    pagination = query.order_by(Invoice.created_at.desc()).paginate(page=page, per_page=per_page)
    invoices = pagination.items
    
    return render_template('invoices.html', 
                          invoices=invoices, 
                          pagination=pagination,
                          seller=seller,
                          invoice_no=invoice_no,
                          date_from=invoice_date_from,
                          date_to=invoice_date_to,
                          amount_from=amount_from,
                          amount_to=amount_to)

@app.route('/invoice/<int:id>')
@login_required
def invoice_detail(id):
    """发票详情页面"""
    invoice = Invoice.query.get_or_404(id)
    
    # 检查权限
    if invoice.user_id != current_user.id:
        flash('无权限查看此发票')
        return redirect(url_for('invoices'))
    
    return render_template('invoice_detail.html', invoice=invoice)

@app.route('/invoice/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_invoice(id):
    """编辑发票信息"""
    invoice = Invoice.query.get_or_404(id)
    
    # 检查权限
    if invoice.user_id != current_user.id:
        flash('无权限编辑此发票')
        return redirect(url_for('invoices'))
    
    if request.method == 'POST':
        # 更新发票信息
        invoice.invoice_no = request.form.get('invoice_no', '')
        invoice.invoice_date = request.form.get('invoice_date', '')
        invoice.seller = request.form.get('seller', '')
        invoice.amount = request.form.get('amount', '')
        invoice.project_name = request.form.get('project_name', '')
        invoice.notes = request.form.get('notes', '')
        
        # 预处理文件名中的特殊字符
        invoice_date_clean = invoice.invoice_date.replace('/', '-').replace(' ', '')
        seller_clean = invoice.seller[:20].replace('/', '_')
        # 处理反斜杠，避免在f-string表达式中使用
        seller_clean = seller_clean.replace('\\', '_')
        
        # 更新文件名
        new_filename = f"[{invoice_date_clean}-{seller_clean}-{invoice.amount}-{invoice.invoice_no}].pdf"
        new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
        invoice.current_filename = new_filename
        
        db.session.commit()
        flash('发票信息已更新')
        return redirect(url_for('invoice_detail', id=invoice.id))
    
    return render_template('edit_invoice.html', invoice=invoice)

@app.route('/invoice/<int:id>/download')
@login_required
def download_invoice(id):
    """下载单张发票"""
    invoice = Invoice.query.get_or_404(id)
    
    # 检查权限
    if invoice.user_id != current_user.id:
        flash('无权限下载此发票')
        return redirect(url_for('invoices'))
    
    # 检查文件是否存在
    if not os.path.exists(invoice.file_path):
        flash('发票文件不存在')
        return redirect(url_for('invoice_detail', id=invoice.id))
    
    return send_file(invoice.file_path, 
                    as_attachment=True, 
                    download_name=invoice.current_filename)

@app.route('/invoice/<int:id>/delete', methods=['POST'])
@login_required
def delete_invoice(id):
    """删除发票"""
    invoice = Invoice.query.get_or_404(id)
    
    # 检查权限
    if invoice.user_id != current_user.id:
        flash('无权限删除此发票')
        return redirect(url_for('invoices'))
    
    # 删除文件
    try:
        if os.path.exists(invoice.file_path):
            os.remove(invoice.file_path)
    except Exception as e:
        flash(f'删除文件时出错: {str(e)}')
    
    # 删除数据库记录
    db.session.delete(invoice)
    db.session.commit()
    
    flash('发票已删除')
    return redirect(url_for('invoices'))

@app.route('/invoices/batch_download', methods=['POST'])
@login_required
def batch_download_invoices():
    """批量下载发票"""
    invoice_ids = request.form.getlist('invoice_ids')
    
    if not invoice_ids:
        flash('请选择要下载的发票')
        return redirect(url_for('invoices'))
    
    # 创建临时目录
    temp_dir = f'temp_batch_{current_user.id}_{datetime.now().strftime("%Y%m%d%H%M%S")}'
    os.makedirs(temp_dir, exist_ok=True)
    
    # 复制选中的发票到临时目录
    for invoice_id in invoice_ids:
        invoice = Invoice.query.get(invoice_id)
        if invoice and invoice.user_id == current_user.id and os.path.exists(invoice.file_path):
            shutil.copy2(invoice.file_path, os.path.join(temp_dir, invoice.current_filename))
    
    # 创建CSV文件
    csv_path = os.path.join(temp_dir, "发票信息汇总.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['发票号码', '开票日期', '开票方名称', '含税金额', '项目名称', '文件名'])
        writer.writeheader()
        
        for invoice_id in invoice_ids:
            invoice = Invoice.query.get(invoice_id)
            if invoice and invoice.user_id == current_user.id:
                writer.writerow({
                    '发票号码': invoice.invoice_no,
                    '开票日期': invoice.invoice_date,
                    '开票方名称': invoice.seller,
                    '含税金额': invoice.amount,
                    '项目名称': invoice.project_name,
                    '文件名': invoice.current_filename
                })
    
    # 创建ZIP文件
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_filename = f"selected_invoices_{timestamp}.zip"
    user_static_dir = os.path.join('static', f'user_{current_user.id}')
    os.makedirs(user_static_dir, exist_ok=True)
    zip_path = os.path.join(user_static_dir, zip_filename)
    
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, os.path.basename(file_path))
    
    # 清理临时目录
    shutil.rmtree(temp_dir)
    
    return redirect(url_for('download_file', user_id=current_user.id, filename=zip_filename))

@app.route('/invoices/batch_delete', methods=['POST'])
@login_required
def batch_delete_invoices():
    """批量删除发票"""
    invoice_ids = request.form.getlist('invoice_ids')
    
    if not invoice_ids:
        flash('请选择要删除的发票')
        return redirect(url_for('invoices'))
    
    deleted_count = 0
    error_count = 0
    
    for invoice_id in invoice_ids:
        try:
            invoice = Invoice.query.get(invoice_id)
            if invoice and invoice.user_id == current_user.id:
                # 尝试删除文件
                try:
                    if os.path.exists(invoice.file_path):
                        os.remove(invoice.file_path)
                except Exception as e:
                    print(f"删除文件时出错: {str(e)}")
                
                # 删除数据库记录
                db.session.delete(invoice)
                deleted_count += 1
        except Exception as e:
            error_count += 1
            print(f"删除发票 {invoice_id} 时出错: {str(e)}")
    
    # 提交数据库更改
    db.session.commit()
    
    if error_count > 0:
        flash(f'成功删除 {deleted_count} 张发票，{error_count} 张发票删除失败')
    else:
        flash(f'成功删除 {deleted_count} 张发票')
    
    return redirect(url_for('invoices'))

def create_tables():
    """创建数据库表"""
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    # 确保 downloads 目录存在
    os.makedirs('downloads', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # 创建数据库表
    create_tables()
    
    # 获取端口和主机配置
    port = Config.get_port()
    host = Config.get_host()
    
    # 启动时打印配置信息
    print("\n=== 当前配置信息 ===")
    print(f"模型: {Config.get_model()}")
    print(f"API基础URL: {Config.get_api_base()}")
    print(f"监听地址: {host}:{port}")
    print("===================\n")
    
    app.run(host=host, port=port, debug=True)