from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """用户模型"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True)
    email = db.Column(db.String(120), unique=True, index=True)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    def set_password(self, password):
        """设置密码哈希"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

class EmailAccount(db.Model):
    """用户保存的邮箱账号"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    email_address = db.Column(db.String(120), nullable=False)
    password = db.Column(db.String(128), nullable=False)  # 应该加密存储
    description = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('email_accounts', lazy=True))
    
    def __repr__(self):
        return f'<EmailAccount {self.email_address}>'

class InvoiceHistory(db.Model):
    """用户的发票处理历史"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'))
    search_date = db.Column(db.Date, nullable=True)
    invoice_count = db.Column(db.Integer, default=0)
    zip_filename = db.Column(db.String(128), nullable=True)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('invoice_histories', lazy=True))
    email_account = db.relationship('EmailAccount', backref=db.backref('invoice_histories', lazy=True))
    
    def __repr__(self):
        return f'<InvoiceHistory {self.id} - {self.invoice_count} invoices>'

class Invoice(db.Model):
    """发票信息模型"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    history_id = db.Column(db.Integer, db.ForeignKey('invoice_history.id'), nullable=True)
    
    # 发票基本信息
    invoice_no = db.Column(db.String(50), index=True)  # 发票号码
    invoice_date = db.Column(db.String(20))  # 开票日期
    seller = db.Column(db.String(100))  # 开票方名称
    amount = db.Column(db.String(20))  # 含税金额
    project_name = db.Column(db.String(200))  # 项目名称
    
    # 文件信息
    original_filename = db.Column(db.String(200))  # 原始文件名
    current_filename = db.Column(db.String(200))  # 当前文件名
    file_path = db.Column(db.String(500))  # 文件路径
    
    # 元数据
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)  # 备注
    
    # 关系
    user = db.relationship('User', backref=db.backref('invoices', lazy=True))
    history = db.relationship('InvoiceHistory', backref=db.backref('invoices', lazy=True))
    
    def __repr__(self):
        return f'<Invoice {self.invoice_no} - {self.amount}>' 