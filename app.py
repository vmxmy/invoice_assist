from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, session
import os
import time
import json
import pdfplumber
import requests
import zipfile
import shutil
import csv
import re
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

# 添加SERVER_NAME配置，用于在非请求上下文中生成URL
# 注意：这个配置在开发环境中可能会导致一些问题，如果遇到问题可以移除
# app.config['SERVER_NAME'] = os.getenv('SERVER_NAME', 'localhost:5001')

# 初始化数据库
db.init_app(app)

# 初始化登录管理器
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录以访问此页面。'

@login_manager.user_loader
def load_user(user_id):
    # 使用Session.get()代替Query.get()
    return db.session.get(User, int(user_id))

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
        base_url = os.getenv('OPENAI_API_BASE') or 'https://api.openai.com'
        # 确保基础URL不以斜杠结尾
        if base_url.endswith('/'):
            base_url = base_url[:-1]
        return base_url
    
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
        - invoice_date (开票日期，必须使用YYYY-MM-DD格式，例如2023-01-01)
        - seller (开票方名称)
        - amount (含税金额，只需数字)
        - project_name (项目名称，从货物或应税劳务、服务名称中提取)
        - invoice_no (发票号码)

        发票文本内容：
        {text}

        请只返回JSON格式的数据，不要有其他说明文字。格式如下：
        {{
            "invoice_date": "YYYY-MM-DD",
            "seller": "公司名称",
            "amount": "金额数字",
            "project_name": "项目名称",
            "invoice_no": "发票号码"
        }}
        
        特别注意：日期必须是YYYY-MM-DD格式，如果原始日期是中文格式如"2023年01月01日"，请转换为"2023-01-01"。
        """

        # 获取API基础URL
        api_base = Config.get_api_base()
        
        # 构建完整的API端点URL
        api_endpoint = f"{api_base}/v1/chat/completions"

        # 调用自定义 OpenAI 代理服务器
        response = requests.post(
            api_endpoint,
            json={
                "model": Config.get_model(),
                "messages": [
                    {"role": "system", "content": "你是一个专门处理发票信息的助手，请严格按照要求的JSON格式返回提取的信息。日期必须使用YYYY-MM-DD格式。如果是机票或者火车票，请务必把【出发地-目的地，出发日期，出发时间，航班号/车次，舱位等级】填入项目名称。"},
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
        
        # 初始化content变量，避免后续引用错误
        content = ""
        
        # 解析响应
        try:
            result = response.json()
            actual_model = result.get('model', 'unknown')  # 获取实际使用的模型
            
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
            
            # 确保日期格式为YYYY-MM-DD
            if 'invoice_date' in extracted_info:
                date_str = extracted_info['invoice_date']
                # 如果日期是中文格式，转换为标准格式
                chinese_pattern = r'(\d{4})年(\d{1,2})月(\d{1,2})日'
                match = re.search(chinese_pattern, date_str)
                if match:
                    year, month, day = match.groups()
                    extracted_info['invoice_date'] = f"{year}-{int(month):02d}-{int(day):02d}"
                
                # 确保日期格式正确
                date_pattern = r'^\d{4}-\d{2}-\d{2}$'
                if not re.match(date_pattern, extracted_info['invoice_date']):
                    print(f"警告: 日期格式不正确: {extracted_info['invoice_date']}")
                    # 尝试解析并重新格式化日期
                    try:
                        from datetime import datetime
                        # 尝试多种格式
                        for fmt in ['%Y年%m月%d日', '%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d']:
                            try:
                                dt = datetime.strptime(date_str, fmt)
                                extracted_info['invoice_date'] = dt.strftime('%Y-%m-%d')
                                break
                            except ValueError:
                                continue
                    except Exception as e:
                        print(f"日期格式化失败: {e}")
            
            # 添加文件名
            extracted_info['filename'] = os.path.basename(pdf_path)
            extracted_info['filepath'] = pdf_path
            return extracted_info
        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            print(f"尝试解析的内容: {content}")
            return None
        except Exception as e:
            print(f"API响应解析错误: {e}")
            print(f"响应内容: {response.text}")
            return None
            
    except requests.RequestException as e:
        print(f"API请求错误: {e}")
        return None
    except Exception as e:
        print(f"处理过程中出现错误: {e}")
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
            
            # 去掉方括号，直接使用连字符分隔
            new_filename = f"{invoice_date}-{seller}-{amount}-{invoice_no}.pdf"
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
            
            # 构建新文件名 - 去掉方括号，直接使用连字符分隔
            new_filename = f"{invoice_date}-{seller}-{amount}-{invoice_no}.pdf"
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

def create_invoice_zip(renamed_dir, user_id=None):
    """创建发票文件的ZIP压缩包"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    zip_filename = f"invoices_{timestamp}.zip"
    
    # 如果没有提供user_id，则使用current_user.id
    if user_id is None and current_user and hasattr(current_user, 'id'):
        user_id = current_user.id
    
    # 为每个用户创建单独的目录
    user_static_dir = os.path.join('static', f'user_{user_id}')
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

def save_invoice_to_db(invoice_info, history_id=None, user_id=None):
    """将发票信息保存到数据库"""
    try:
        # 如果没有提供user_id，则使用current_user.id
        if user_id is None and current_user and hasattr(current_user, 'id'):
            user_id = current_user.id
        
        # 如果没有user_id，则无法保存
        if not user_id:
            print("无法保存发票：未提供user_id")
            return None
        
        # 获取原始文件名
        original_filename = invoice_info.get('filename', '')
        
        # 获取发票日期（应该已经是YYYY-MM-DD格式）
        invoice_date = invoice_info.get('invoice_date', '未知日期')
        
        # 构建重命名后的文件名
        seller = invoice_info.get('seller', '未知开票方').replace('/', '_')
        # 单独处理反斜杠
        seller = seller.replace('\\', '_')
        if len(seller) > 20:
            seller = seller[:20]
        amount = invoice_info.get('amount', '未知金额')
        invoice_no = invoice_info.get('invoice_no', '未知发票号')
        
        # 去掉方括号，直接使用连字符分隔
        new_filename = f"{invoice_date}-{seller}-{amount}-{invoice_no}.pdf"
        new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
        
        # 打印调试信息
        print(f"保存发票到数据库: 原始文件名={original_filename}, 新文件名={new_filename}")
        print(f"发票信息: 发票号={invoice_no}, 日期={invoice_date}, 卖家={seller}, 金额={amount}")
        
        # 创建发票记录
        invoice = Invoice(
            user_id=user_id,
            history_id=history_id,
            invoice_no=invoice_info.get('invoice_no', ''),
            invoice_date=invoice_info.get('invoice_date', ''),
            seller=invoice_info.get('seller', ''),
            amount=invoice_info.get('amount', ''),
            project_name=invoice_info.get('project_name', ''),
            original_filename=original_filename,
            current_filename=new_filename,
            file_path=invoice_info.get('filepath', '')
        )
        
        # 打印发票对象信息
        print(f"创建的发票对象: user_id={invoice.user_id}, invoice_no={invoice.invoice_no}, invoice_date={invoice.invoice_date}")
        
        # 添加到数据库会话
        db.session.add(invoice)
        
        # 提交更改
        db.session.commit()
        
        # 验证发票是否成功保存
        saved_invoice = Invoice.query.filter_by(user_id=user_id, invoice_no=invoice_no).first()
        if saved_invoice:
            print(f"发票成功保存到数据库: ID={saved_invoice.id}")
        else:
            print(f"警告：发票可能未成功保存到数据库")
        
        return invoice
    except Exception as e:
        # 回滚事务
        db.session.rollback()
        print(f"保存发票到数据库时出错: {str(e)}")
        # 尝试再次保存，使用新的会话
        try:
            print("尝试使用新会话重新保存发票...")
            with app.app_context():
                # 创建新的会话
                new_invoice = Invoice(
                    user_id=user_id,
                    history_id=history_id,
                    invoice_no=invoice_info.get('invoice_no', ''),
                    invoice_date=invoice_info.get('invoice_date', ''),
                    seller=invoice_info.get('seller', ''),
                    amount=invoice_info.get('amount', ''),
                    project_name=invoice_info.get('project_name', ''),
                    original_filename=original_filename,
                    current_filename=new_filename,
                    file_path=invoice_info.get('filepath', '')
                )
                db.session.add(new_invoice)
                db.session.commit()
                print(f"使用新会话成功保存发票: ID={new_invoice.id}")
                return new_invoice
        except Exception as e2:
            print(f"使用新会话保存发票时再次出错: {str(e2)}")
            db.session.rollback()
            return None

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
    # 使用Session.get()代替Query.get_or_404()
    account = db.session.get(EmailAccount, id)
    if not account:
        flash('邮箱账号不存在')
        return redirect(url_for('email_accounts'))
    
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
        # 获取表单数据
        email = form.email_account.data
        password = form.password.data
        search_date = form.search_date.data.strftime('%Y-%m-%d') if form.search_date.data else ''
        
        # 将数据存储在会话中，而不是URL参数
        session['email_for_download'] = email
        session['password_for_download'] = password
        session['search_date_for_download'] = search_date
        
        # 重定向到处理页面
        return redirect(url_for('show_processing'))
    
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
    
    # 从会话中获取数据
    email = session.get('email_for_download')
    password = session.get('password_for_download')
    search_date = session.get('search_date_for_download')
    
    # 清除会话中的敏感数据
    session.pop('email_for_download', None)
    session.pop('password_for_download', None)
    session.pop('search_date_for_download', None)
    
    if not email or not password:
        flash('请输入邮箱和密码')
        return redirect(url_for('download_invoices'))
    
    # 在线程启动前保存用户ID
    user_id = current_user.id
    
    # 启动后台处理线程
    import threading
    thread = threading.Thread(target=process_invoices_thread, args=(email, password, search_date, user_id))
    thread.daemon = True
    thread.start()
    
    return render_template('processing.html')

def process_invoices_thread(email, password, search_date, user_id):
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
        
        try:
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
            
            # 获取下载的文件列表并提取信息
            downloads_dir = 'downloads'
            invoice_info = []
            duplicate_invoices = []  # 存储重复的发票信息
            new_invoices = []  # 存储新的发票信息
            saved_invoices = []  # 存储成功保存到数据库的发票
            
            if os.path.exists(downloads_dir):
                files = [f for f in os.listdir(downloads_dir) if f.lower().endswith('.pdf')]
                processing_status['total'] = len(files)
                
                if len(files) == 0:
                    processing_status['status'] = 'complete'
                    processing_status['message'] = '没有找到新的发票文件'
                    processing_status['redirect_url'] = "/download_invoices"
                    return
                
                for i, file in enumerate(files):
                    processing_status['current'] = i + 1
                    file_path = os.path.join(downloads_dir, file)
                    processing_status['current_file'] = file
                    
                    info = extract_invoice_info(file_path)
                    if info:
                        invoice_info.append(info)
                
                # 创建处理历史
                history_id = None
                with app.app_context():
                    try:
                        # 获取用户对象
                        user = db.session.get(User, user_id)
                        if not user:
                            processing_status['status'] = 'error'
                            processing_status['error'] = '用户会话已过期，请重新登录'
                            return
                        
                        history = InvoiceHistory(
                            user_id=user_id,
                            search_date=date_since.date() if date_since else None,
                            invoice_count=0,  # 先设为0，后面再更新
                            processed_at=datetime.utcnow()
                        )
                        
                        # 查找是否使用了保存的邮箱账号
                        email_account = EmailAccount.query.filter_by(user_id=user_id, email_address=email).first()
                        if email_account:
                            history.email_account_id = email_account.id
                        
                        db.session.add(history)
                        db.session.commit()
                        history_id = history.id
                        print(f"成功创建处理历史记录，ID: {history_id}")
                    except Exception as e:
                        print(f"创建处理历史时出错: {e}")
                        processing_status['status'] = 'error'
                        processing_status['error'] = f'创建处理历史时出错: {str(e)}'
                        return
                
                # 检查每张发票是否已存在
                processing_status['current_file'] = '正在检查重复发票...'
                for info in invoice_info:
                    try:
                        with app.app_context():
                            # 创建新的数据库会话
                            is_duplicate = False
                            try:
                                is_duplicate = check_duplicate_invoice(info, user_id)
                            except Exception as check_error:
                                print(f"检查重复发票时出错: {check_error}")
                                # 继续处理，假设不是重复的
                                is_duplicate = False
                            
                            if is_duplicate:
                                # 发票已存在，添加到重复列表
                                duplicate_invoices.append(info)
                                print(f"发现重复发票: {info.get('invoice_no', '')}")
                            else:
                                # 新发票，添加到新发票列表
                                new_invoices.append(info)
                                print(f"发现新发票: {info.get('invoice_no', '')}")
                                
                                # 立即保存到数据库，确保即使处理中断也能保存部分结果
                                try:
                                    saved_invoice = save_invoice_to_db(info, history_id, user_id)
                                    if saved_invoice:
                                        print(f"成功保存发票到数据库: ID={saved_invoice.id}, 发票号={saved_invoice.invoice_no}")
                                        saved_invoices.append(saved_invoice)
                                    else:
                                        print(f"保存发票失败，返回值为None: {info.get('invoice_no', '')}")
                                except Exception as save_error:
                                    print(f"保存发票到数据库时出错: {save_error}")
                                    # 继续处理其他发票
                    except Exception as e:
                        print(f"处理发票时出错: {e}")
                        # 继续处理其他发票
                
                # 只处理新发票
                zip_filename = None
                if new_invoices:
                    processing_status['current_file'] = '正在重命名文件...'
                    # 重命名文件并创建CSV
                    renamed_dir, renamed_files = rename_invoice_files(new_invoices)
                    
                    processing_status['current_file'] = '正在创建ZIP文件...'
                    # 创建ZIP文件
                    zip_filename = create_invoice_zip(renamed_dir, user_id)
                    
                    # 更新处理历史
                    if history_id:
                        try:
                            with app.app_context():
                                # 使用Session.get()代替Query.get()
                                history = db.session.get(InvoiceHistory, history_id)
                                if history:
                                    # 使用实际保存成功的发票数量
                                    history.invoice_count = len(saved_invoices)
                                    history.zip_filename = zip_filename
                                    db.session.commit()
                                    print(f"成功更新处理历史记录: ID={history_id}, 发票数量={len(saved_invoices)}")
                        except Exception as e:
                            print(f"更新处理历史时出错: {e}")
                else:
                    # 没有新发票，更新处理历史
                    if history_id:
                        try:
                            with app.app_context():
                                # 使用Session.get()代替Query.get()
                                history = db.session.get(InvoiceHistory, history_id)
                                if history:
                                    history.invoice_count = 0
                                    db.session.commit()
                                    print(f"没有新发票，更新处理历史记录: ID={history_id}, 发票数量=0")
                        except Exception as e:
                            print(f"更新处理历史时出错: {e}")
                
                # 计算处理时间
                processing_time = time.time() - start_time
                
                # 构建提示信息
                date_message = f"，检索{search_date}之后的邮件" if search_date else ""
                duplicate_message = f"，其中 {len(duplicate_invoices)} 张为重复发票" if duplicate_invoices else ""
                
                # 存储处理结果信息
                processing_status['message'] = f'''成功下载并处理 {len(files)} 个文件{date_message}
发现 {len(invoice_info)} 张发票，成功导入 {len(saved_invoices)} 张新发票{duplicate_message}
处理时间: {processing_time:.2f} 秒
本次下载: {downloaded_count} 个PDF附件
本次处理使用的大模型：{Config.get_model()}'''
                
                # 设置处理完成状态
                processing_status['status'] = 'complete'
                
                # 不使用url_for，直接构建URL路径
                if saved_invoices and zip_filename:
                    # 如果有新发票，跳转到结果页面
                    processing_status['redirect_url'] = f"/invoice_results?new_count={len(saved_invoices)}&dup_count={len(duplicate_invoices)}&zip_file=/static/user_{user_id}/{zip_filename}"
                else:
                    # 没有新发票，跳转到下载页面
                    processing_status['redirect_url'] = "/download_invoices"
            else:
                processing_status['status'] = 'error'
                processing_status['error'] = '没有找到发票附件'
        finally:
            # 确保IMAP连接正确关闭
            try:
                imap.logout()
            except Exception as e:
                print(f"关闭IMAP连接时出错: {e}")
            
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
    
    # 获取新导入的发票，确保包含所有字段
    invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).limit(new_count).all()
    
    # 打印调试信息，查看发票对象是否包含文件名
    for invoice in invoices:
        print(f"发票ID: {invoice.id}, 原始文件名: {invoice.original_filename}, 当前文件名: {invoice.current_filename}")
    
    # 获取重复的发票信息（这里只能显示基本信息，因为详细信息已经在处理过程中丢失）
    duplicate_info = []
    
    # 显示处理结果消息
    if processing_status.get('message'):
        flash(processing_status['message'])
    
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
    
    # 检查是否有正在进行的处理
    current_processing = False
    processing_message = ""
    if processing_status['status'] == 'processing':
        current_processing = True
        if processing_status['total'] > 0:
            progress = f"{processing_status['current']}/{processing_status['total']}"
            processing_message = f"正在处理发票 ({progress})，当前文件: {processing_status['current_file']}"
        else:
            processing_message = f"正在处理发票，当前步骤: {processing_status['current_file']}"
    
    return render_template('history.html', 
                          histories=histories, 
                          current_processing=current_processing,
                          processing_message=processing_message)

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
    per_page = request.args.get('per_page', 10, type=int)
    
    # 获取筛选参数
    seller = request.args.get('seller', '')
    invoice_no = request.args.get('invoice_no', '')
    invoice_date_from = request.args.get('date_from', '')
    invoice_date_to = request.args.get('date_to', '')
    amount_from = request.args.get('amount_from', '')
    amount_to = request.args.get('amount_to', '')
    
    # 获取排序参数
    sort_by = request.args.get('sort_by', '')
    sort_order = request.args.get('sort_order', 'asc')
    
    # 打印筛选和排序参数，用于调试
    print(f"筛选参数: seller={seller}, invoice_no={invoice_no}, date_from={invoice_date_from}, date_to={invoice_date_to}, amount_from={amount_from}, amount_to={amount_to}")
    print(f"排序参数: sort_by={sort_by}, sort_order={sort_order}")
    print(f"分页参数: page={page}, per_page={per_page}")
    
    try:
        # 构建查询
        query = Invoice.query.filter_by(user_id=current_user.id)
        
        # 打印初始查询结果数量
        initial_count = query.count()
        print(f"初始查询结果: {initial_count} 条记录")
        
        # 检查数据库中是否有记录
        if initial_count == 0:
            print("警告: 数据库中没有发票记录")
            # 检查用户ID是否正确
            print(f"当前用户ID: {current_user.id}")
            # 检查是否有任何发票记录
            total_invoices = Invoice.query.count()
            print(f"数据库中的总发票数: {total_invoices}")
            if total_invoices > 0:
                # 如果有发票但不属于当前用户，打印一些示例
                sample_invoices = Invoice.query.limit(3).all()
                for inv in sample_invoices:
                    print(f"示例发票: ID={inv.id}, 用户ID={inv.user_id}, 发票号={inv.invoice_no}")
        
        # 应用筛选条件
        if seller:
            query = query.filter(Invoice.seller.like(f'%{seller}%'))
            print(f"应用卖家筛选后的记录数: {query.count()}")
        
        if invoice_no:
            query = query.filter(Invoice.invoice_no.like(f'%{invoice_no}%'))
            print(f"应用发票号筛选后的记录数: {query.count()}")
        
        # 使用invoice_date字段进行日期筛选
        if invoice_date_from:
            try:
                # 将输入日期转换为标准格式
                date_from_obj = datetime.strptime(invoice_date_from, '%Y-%m-%d')
                date_from_str = date_from_obj.strftime('%Y-%m-%d')
                
                # 简化日期筛选逻辑，使用简单的字符串比较
                query = query.filter(Invoice.invoice_date >= date_from_str)
                print(f"应用起始日期筛选后的记录数: {query.count()}")
            except ValueError as e:
                flash('起始日期格式无效，请使用YYYY-MM-DD格式')
                print(f"日期解析错误: {str(e)}")
        
        if invoice_date_to:
            try:
                # 将输入日期转换为标准格式
                date_to_obj = datetime.strptime(invoice_date_to, '%Y-%m-%d')
                date_to_str = date_to_obj.strftime('%Y-%m-%d')
                
                # 简化日期筛选逻辑，使用简单的字符串比较
                query = query.filter(Invoice.invoice_date <= date_to_str)
                print(f"应用结束日期筛选后的记录数: {query.count()}")
            except ValueError as e:
                flash('结束日期格式无效，请使用YYYY-MM-DD格式')
                print(f"日期解析错误: {str(e)}")
        
        # 添加金额筛选
        if amount_from:
            try:
                # 尝试将金额转换为数字进行比较
                amount_from_float = float(amount_from)
                # 使用SQL函数将字符串转换为数字进行比较
                from sqlalchemy import cast, Float
                query = query.filter(cast(Invoice.amount, Float) >= amount_from_float)
                print(f"应用最小金额筛选后的记录数: {query.count()}")
            except ValueError:
                flash('最小金额格式无效，请输入有效数字')
        
        if amount_to:
            try:
                amount_to_float = float(amount_to)
                from sqlalchemy import cast, Float
                query = query.filter(cast(Invoice.amount, Float) <= amount_to_float)
                print(f"应用最大金额筛选后的记录数: {query.count()}")
            except ValueError:
                flash('最大金额格式无效，请输入有效数字')
        
        # 应用排序
        if sort_by:
            # 获取排序字段
            if sort_by == 'invoice_no':
                sort_column = Invoice.invoice_no
            elif sort_by == 'invoice_date':
                sort_column = Invoice.invoice_date
            elif sort_by == 'seller':
                sort_column = Invoice.seller
            elif sort_by == 'amount':
                # 对金额进行数值排序
                from sqlalchemy import cast, Float
                sort_column = cast(Invoice.amount, Float)
            elif sort_by == 'project_name':
                sort_column = Invoice.project_name
            else:
                # 默认按创建时间降序排序
                sort_column = Invoice.created_at
                sort_order = 'desc'
            
            # 根据排序顺序应用排序
            if sort_order == 'desc':
                query = query.order_by(sort_column.desc())
            else:
                query = query.order_by(sort_column.asc())
            
            print(f"应用排序: {sort_by} {sort_order}")
        else:
            # 默认按创建时间降序排序
            query = query.order_by(Invoice.created_at.desc())
        
        # 打印最终查询结果数量
        final_count = query.count()
        print(f"最终查询结果: {final_count} 条记录")
        
        # 分页获取发票
        pagination = query.paginate(page=page, per_page=per_page)
        invoices = pagination.items
        
        # 打印结果数量和日期格式，用于调试
        print(f"分页后的查询结果: 共找到 {len(invoices)} 条记录")
        if invoices:
            print(f"第一条记录的日期: {invoices[0].invoice_date}")
        else:
            # 如果没有找到记录，尝试获取所有发票并打印前几条记录的信息
            all_invoices = Invoice.query.filter_by(user_id=current_user.id).limit(5).all()
            print(f"数据库中的发票记录示例:")
            for inv in all_invoices:
                print(f"发票ID: {inv.id}, 日期: '{inv.invoice_date}', 卖家: '{inv.seller}', 金额: '{inv.amount}'")
        
        return render_template('invoices.html', 
                              invoices=invoices, 
                              pagination=pagination,
                              seller=seller,
                              invoice_no=invoice_no,
                              date_from=invoice_date_from,
                              date_to=invoice_date_to,
                              amount_from=amount_from,
                              amount_to=amount_to)
    except Exception as e:
        print(f"发票查询过程中出错: {str(e)}")
        flash(f'查询发票时出错: {str(e)}')
        return render_template('invoices.html', 
                              invoices=[], 
                              pagination=None,
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
    # 使用Session.get()代替Query.get_or_404()
    invoice = db.session.get(Invoice, id)
    if not invoice:
        flash('发票不存在')
        return redirect(url_for('invoices'))
    
    # 检查权限
    if invoice.user_id != current_user.id:
        flash('无权限查看此发票')
        return redirect(url_for('invoices'))
    
    return render_template('invoice_detail.html', invoice=invoice)

@app.route('/invoice/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_invoice(id):
    """编辑发票信息"""
    # 使用Session.get()代替Query.get_or_404()
    invoice = db.session.get(Invoice, id)
    if not invoice:
        flash('发票不存在')
        return redirect(url_for('invoices'))
    
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
        
        # 确保日期格式是标准的YYYY-MM-DD
        try:
            # 尝试解析日期并重新格式化
            from datetime import datetime
            for fmt in ['%Y年%m月%d日', '%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d']:
                try:
                    dt = datetime.strptime(invoice.invoice_date, fmt)
                    invoice.invoice_date = dt.strftime('%Y-%m-%d')
                    print(f"成功解析并转换日期: {invoice.invoice_date}")
                    break
                except ValueError:
                    continue
        except Exception as e:
            print(f"日期格式化失败: {str(e)}")
        
        # 预处理文件名中的特殊字符
        invoice_date_clean = invoice.invoice_date
        seller_clean = invoice.seller[:20].replace('/', '_')
        # 处理反斜杠，避免在f-string表达式中使用
        seller_clean = seller_clean.replace('\\', '_')
        
        # 更新文件名 - 去掉方括号，直接使用连字符分隔
        new_filename = f"{invoice_date_clean}-{seller_clean}-{invoice.amount}-{invoice.invoice_no}.pdf"
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
    # 使用Session.get()代替Query.get_or_404()
    invoice = db.session.get(Invoice, id)
    if not invoice:
        flash('发票不存在')
        return redirect(url_for('invoices'))
    
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
    # 使用Session.get()代替Query.get_or_404()
    invoice = db.session.get(Invoice, id)
    if not invoice:
        flash('发票不存在')
        return redirect(url_for('invoices'))
    
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
        # 使用Session.get()代替Query.get()
        invoice = db.session.get(Invoice, invoice_id)
        if invoice and invoice.user_id == current_user.id and os.path.exists(invoice.file_path):
            shutil.copy2(invoice.file_path, os.path.join(temp_dir, invoice.current_filename))
    
    # 创建CSV文件
    csv_path = os.path.join(temp_dir, "发票信息汇总.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=['发票号码', '开票日期', '开票方名称', '含税金额', '项目名称', '文件名'])
        writer.writeheader()
        
        for invoice_id in invoice_ids:
            # 使用Session.get()代替Query.get()
            invoice = db.session.get(Invoice, invoice_id)
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
            # 使用Session.get()代替Query.get()
            invoice = db.session.get(Invoice, invoice_id)
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

@app.route('/invoices/clear_filters')
@login_required
def clear_invoice_filters():
    """清除发票筛选条件"""
    return redirect(url_for('invoices'))

@app.route('/normalize_dates')
@login_required
def normalize_dates():
    """将所有发票的日期格式标准化为YYYY-MM-DD"""
    import re
    
    # 获取当前用户的所有发票
    invoices = Invoice.query.filter_by(user_id=current_user.id).all()
    updated_count = 0
    
    for invoice in invoices:
        original_date = invoice.invoice_date
        
        # 如果日期为空，跳过
        if not original_date:
            continue
        
        # 检查是否已经是标准格式
        if re.match(r'^\d{4}-\d{2}-\d{2}$', original_date):
            continue
        
        # 尝试转换中文日期格式 (YYYY年MM月DD日)
        chinese_pattern = r'(\d{4})年(\d{1,2})月(\d{1,2})日'
        match = re.search(chinese_pattern, original_date)
        if match:
            year, month, day = match.groups()
            invoice.invoice_date = f"{year}-{int(month):02d}-{int(day):02d}"
            updated_count += 1
            continue
        
        # 尝试其他常见格式
        try:
            for fmt in ['%Y/%m/%d', '%Y.%m.%d', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(original_date, fmt)
                    invoice.invoice_date = dt.strftime('%Y-%m-%d')
                    updated_count += 1
                    break
                except ValueError:
                    continue
        except Exception as e:
            print(f"日期格式化失败: {str(e)} - 发票ID: {invoice.id}, 日期: {original_date}")
    
    # 提交更改
    db.session.commit()
    
    flash(f'成功更新 {updated_count} 张发票的日期格式为标准格式 (YYYY-MM-DD)')
    return redirect(url_for('invoices'))

@app.route('/repair_database')
@login_required
def repair_database():
    """检查并修复数据库中的发票记录"""
    # 只允许管理员执行此操作
    if not current_user.is_admin:
        flash('您没有权限执行此操作')
        return redirect(url_for('dashboard'))
    
    try:
        # 获取所有发票记录
        invoices = Invoice.query.all()
        total_count = len(invoices)
        updated_count = 0
        error_count = 0
        
        for invoice in invoices:
            try:
                # 检查发票日期格式
                if invoice.invoice_date:
                    # 尝试标准化日期格式
                    try:
                        for fmt in ['%Y年%m月%d日', '%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d']:
                            try:
                                dt = datetime.strptime(invoice.invoice_date, fmt)
                                invoice.invoice_date = dt.strftime('%Y-%m-%d')
                                break
                            except ValueError:
                                continue
                    except Exception as e:
                        print(f"日期格式化失败: {str(e)}")
                
                # 更新文件名格式
                if invoice.seller:
                    seller_clean = invoice.seller[:20].replace('/', '_').replace('\\', '_')
                    new_filename = f"{invoice.invoice_date}-{seller_clean}-{invoice.amount}-{invoice.invoice_no}.pdf"
                    new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                    
                    if invoice.current_filename != new_filename:
                        invoice.current_filename = new_filename
                        updated_count += 1
            except Exception as e:
                print(f"修复发票记录时出错 (ID: {invoice.id}): {str(e)}")
                error_count += 1
        
        # 提交更改
        db.session.commit()
        
        flash(f'数据库修复完成: 共检查 {total_count} 条记录，更新 {updated_count} 条记录，{error_count} 条记录出错')
    except Exception as e:
        flash(f'数据库修复过程中出错: {str(e)}')
    
    return redirect(url_for('dashboard'))

# 添加一个简单的管理员检查属性
@property
def is_admin(self):
    """检查用户是否为管理员"""
    return self.username == 'admin'  # 简单示例，实际应用中应该有更复杂的逻辑

# 将属性添加到User类
User.is_admin = is_admin

def create_tables():
    """创建数据库表"""
    with app.app_context():
        db.create_all()

def check_duplicate_invoice(invoice_info, user_id=None):
    """检查发票是否已存在"""
    # 如果没有提供user_id，则使用current_user.id
    if user_id is None and current_user and hasattr(current_user, 'id'):
        user_id = current_user.id
    
    # 如果没有user_id，则无法检查
    if not user_id:
        return False
    
    # 检查发票号码是否已存在
    invoice_no = invoice_info.get('invoice_no', '')
    if not invoice_no:
        return False
    
    existing_invoice = Invoice.query.filter_by(
        user_id=user_id,
        invoice_no=invoice_no
    ).first()
    
    return existing_invoice is not None

@app.route('/check_database')
@login_required
def check_database():
    """检查数据库状态，用于诊断问题"""
    try:
        # 获取用户的发票数量
        invoice_count = Invoice.query.filter_by(user_id=current_user.id).count()
        
        # 获取最近的5条发票记录
        recent_invoices = Invoice.query.filter_by(user_id=current_user.id).order_by(Invoice.created_at.desc()).limit(5).all()
        
        # 获取历史记录数量
        history_count = InvoiceHistory.query.filter_by(user_id=current_user.id).count()
        
        # 获取数据库表信息
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        # 获取Invoice表的列信息
        invoice_columns = inspector.get_columns('invoice')
        column_names = [col['name'] for col in invoice_columns]
        
        # 构建诊断信息
        diagnostic_info = {
            'user_id': current_user.id,
            'username': current_user.username,
            'invoice_count': invoice_count,
            'history_count': history_count,
            'tables': tables,
            'invoice_columns': column_names,
            'recent_invoices': []
        }
        
        # 添加最近发票的信息
        for invoice in recent_invoices:
            invoice_info = {
                'id': invoice.id,
                'invoice_no': invoice.invoice_no,
                'invoice_date': invoice.invoice_date,
                'seller': invoice.seller,
                'amount': invoice.amount,
                'created_at': invoice.created_at.strftime('%Y-%m-%d %H:%M:%S') if invoice.created_at else None,
                'current_filename': invoice.current_filename
            }
            diagnostic_info['recent_invoices'].append(invoice_info)
        
        # 返回JSON格式的诊断信息
        return jsonify(diagnostic_info)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/create_test_invoice')
@login_required
def create_test_invoice():
    """创建测试发票记录（用于调试）"""
    try:
        # 创建测试发票
        test_invoice = Invoice(
            user_id=current_user.id,
            invoice_no=f"TEST-{int(time.time())}",
            invoice_date="2023-01-01",
            seller="测试公司",
            amount="100.00",
            project_name="测试项目",
            original_filename="test.pdf",
            current_filename="2023-01-01-测试公司-100.00-TEST.pdf",
            file_path="downloads/test.pdf"
        )
        
        # 添加到数据库
        db.session.add(test_invoice)
        db.session.commit()
        
        flash(f'成功创建测试发票记录: ID={test_invoice.id}')
    except Exception as e:
        db.session.rollback()
        flash(f'创建测试发票记录时出错: {str(e)}')
    
    return redirect(url_for('invoices'))

@app.route('/upload_invoice', methods=['GET', 'POST'])
@login_required
def upload_invoice():
    """手动上传发票"""
    if request.method == 'POST':
        # 检查是否有文件上传
        if 'invoice_file' not in request.files:
            flash('没有选择文件')
            return redirect(request.url)
        
        file = request.files['invoice_file']
        
        # 如果用户没有选择文件，浏览器也会提交一个空的文件
        if file.filename == '':
            flash('没有选择文件')
            return redirect(request.url)
        
        if file and file.filename.lower().endswith('.pdf'):
            # 确保目录存在
            os.makedirs('uploads', exist_ok=True)
            
            # 保存文件
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"manual_upload_{timestamp}.pdf"
            file_path = os.path.join('uploads', filename)
            file.save(file_path)
            
            # 提取发票信息
            info = extract_invoice_info(file_path)
            
            if info:
                # 检查是否为重复发票
                is_duplicate = check_duplicate_invoice(info, current_user.id)
                
                if is_duplicate:
                    flash('发票已存在，无需重复导入')
                else:
                    # 保存到数据库
                    invoice = save_invoice_to_db(info, None, current_user.id)
                    
                    if invoice:
                        flash(f'发票导入成功: {invoice.invoice_no}')
                        return redirect(url_for('invoice_detail', id=invoice.id))
                    else:
                        flash('发票导入失败，请重试')
            else:
                flash('无法从PDF中提取发票信息，请确保上传的是有效的发票文件')
        else:
            flash('只支持上传PDF格式的发票文件')
    
    return render_template('upload_invoice.html')

@app.route('/import_test_data')
@login_required
def import_test_data():
    """导入测试数据（创建多条测试发票记录）"""
    try:
        # 创建测试数据
        test_data = [
            {
                'invoice_no': f"TEST-001-{int(time.time())}",
                'invoice_date': "2023-01-15",
                'seller': "测试公司A",
                'amount': "1000.00",
                'project_name': "测试服务费"
            },
            {
                'invoice_no': f"TEST-002-{int(time.time())}",
                'invoice_date': "2023-02-20",
                'seller': "测试公司B",
                'amount': "2500.50",
                'project_name': "软件开发服务"
            },
            {
                'invoice_no': f"TEST-003-{int(time.time())}",
                'invoice_date': "2023-03-10",
                'seller': "测试公司C",
                'amount': "3800.75",
                'project_name': "硬件设备采购"
            },
            {
                'invoice_no': f"TEST-004-{int(time.time())}",
                'invoice_date': "2023-04-05",
                'seller': "测试公司D",
                'amount': "750.25",
                'project_name': "咨询服务费"
            },
            {
                'invoice_no': f"TEST-005-{int(time.time())}",
                'invoice_date': "2023-05-18",
                'seller': "测试公司E",
                'amount': "1200.00",
                'project_name': "培训费用"
            }
        ]
        
        # 创建处理历史
        history = InvoiceHistory(
            user_id=current_user.id,
            invoice_count=len(test_data),
            processed_at=datetime.utcnow()
        )
        db.session.add(history)
        db.session.commit()
        
        # 保存测试发票
        for data in test_data:
            # 添加必要的文件信息
            data['filename'] = f"test_{data['invoice_no']}.pdf"
            data['filepath'] = os.path.join('downloads', data['filename'])
            
            # 保存到数据库
            invoice = save_invoice_to_db(data, history.id, current_user.id)
            if not invoice:
                print(f"保存测试发票失败: {data['invoice_no']}")
        
        flash(f'成功导入 {len(test_data)} 条测试发票记录')
    except Exception as e:
        db.session.rollback()
        flash(f'导入测试数据时出错: {str(e)}')
    
    return redirect(url_for('invoices'))

@app.route('/clear_history_sql')
@login_required
def clear_history_sql():
    try:
        # 获取当前用户ID
        user_id = current_user.id
        
        # 导入text函数
        from sqlalchemy import text
        
        # 更新发票表，将history_id设为NULL，而不是删除发票记录
        db.session.execute(
            text("UPDATE invoice SET history_id = NULL WHERE user_id = :user_id AND history_id IN (SELECT id FROM invoice_history WHERE user_id = :user_id)"),
            {"user_id": user_id}
        )
        
        # 然后删除历史记录
        db.session.execute(
            text("DELETE FROM invoice_history WHERE user_id = :user_id"),
            {"user_id": user_id}
        )
        
        db.session.commit()
        
        # 显示成功消息
        flash('历史记录已清除', 'success')
        
        # 重定向回历史记录页面
        return redirect(url_for('history'))
    except Exception as e:
        db.session.rollback()
        # 显示错误消息
        flash(f'清除历史记录失败: {str(e)}', 'danger')
        # 重定向回历史记录页面
        return redirect(url_for('history'))

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