from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, DateField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from models import User

class LoginForm(FlaskForm):
    """用户登录表单"""
    username = StringField('用户名', validators=[DataRequired()])
    password = PasswordField('密码', validators=[DataRequired()])
    remember_me = BooleanField('记住我')
    submit = SubmitField('登录')

class RegistrationForm(FlaskForm):
    """用户注册表单"""
    username = StringField('用户名', validators=[DataRequired(), Length(min=3, max=64)])
    email = StringField('邮箱', validators=[DataRequired(), Email()])
    password = PasswordField('密码', validators=[DataRequired(), Length(min=6)])
    password2 = PasswordField('确认密码', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('注册')
    
    def validate_username(self, username):
        """验证用户名是否已存在"""
        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError('该用户名已被使用，请选择其他用户名。')
    
    def validate_email(self, email):
        """验证邮箱是否已存在"""
        user = User.query.filter_by(email=email.data).first()
        if user is not None:
            raise ValidationError('该邮箱已被注册，请使用其他邮箱。')

class EmailAccountForm(FlaskForm):
    """邮箱账号表单"""
    email_address = StringField('邮箱地址', validators=[DataRequired(), Email()])
    password = PasswordField('授权码', validators=[DataRequired()])
    description = StringField('描述', validators=[Length(max=64)])
    submit = SubmitField('保存')

class InvoiceDownloadForm(FlaskForm):
    """发票下载表单"""
    email_account = StringField('邮箱账号', validators=[DataRequired()])
    password = PasswordField('授权码', validators=[DataRequired()])
    search_date = DateField('起始日期', format='%Y-%m-%d', validators=[], render_kw={"placeholder": "YYYY-MM-DD"})
    submit = SubmitField('导入发票') 