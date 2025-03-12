from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """用户模型"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    # 关系
    email_accounts = db.relationship('EmailAccount', backref='user', lazy='dynamic')
    invoice_histories = db.relationship('InvoiceHistory', backref='user', lazy='dynamic')
    invoices = db.relationship('Invoice', backref='user', lazy='dynamic')
    
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
    password = db.Column(db.String(128), nullable=False)  # 实际应用中应加密存储
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<EmailAccount {self.email_address}>'

class InvoiceHistory(db.Model):
    """用户的发票处理历史"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    email_account_id = db.Column(db.Integer, db.ForeignKey('email_account.id'), nullable=True)
    search_date = db.Column(db.Date, nullable=True)
    invoice_count = db.Column(db.Integer, default=0)
    zip_filename = db.Column(db.String(200), nullable=True)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    email_account = db.relationship('EmailAccount', backref='invoice_histories')
    
    def __repr__(self):
        return f'<InvoiceHistory {self.id}>'

class Invoice(db.Model):
    """发票信息模型"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    history_id = db.Column(db.Integer, db.ForeignKey('invoice_history.id'), nullable=True)
    
    # 发票基本信息
    invoice_no = db.Column(db.String(50), nullable=False)
    invoice_date = db.Column(db.String(20), nullable=True)  # 日期字符串，格式为YYYY-MM-DD
    seller = db.Column(db.String(200), nullable=True)
    amount = db.Column(db.String(20), nullable=True)
    project_name = db.Column(db.String(500), nullable=True)
    
    # 文件信息
    original_filename = db.Column(db.String(200), nullable=True)
    current_filename = db.Column(db.String(200), nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    
    # 元数据
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    history = db.relationship('InvoiceHistory', backref='invoices')
    
    def __repr__(self):
        return f'<Invoice {self.invoice_no}>' 