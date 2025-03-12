# 发票下载器

一个基于Flask的Web应用，用于从邮箱导入发票PDF，提取关键信息，并进行管理。

## 主要功能

### 发票导入
- 从邮箱自动导入发票PDF附件
- 支持多种邮箱服务（163、126、QQ、Gmail、Outlook等）
- 可按日期筛选邮件
- 实时显示处理进度
- 自动检测并跳过重复发票

### 发票处理
- 使用AI自动提取发票关键信息（发票号码、开票日期、开票方、金额、项目名称等）
- 根据提取的信息智能重命名发票文件
- 生成发票信息汇总CSV文件
- 将处理后的文件打包下载

### 发票管理
- 查看、编辑、删除发票记录
- 按发票号码、开票方等条件搜索发票
- 下载单张发票或批量下载多张发票
- 查看历史处理记录

### 用户系统
- 多用户支持，每个用户数据相互隔离
- 用户注册、登录功能
- 邮箱账号管理，保存常用邮箱账号

## 技术栈

- 后端：Flask、SQLAlchemy、Flask-Login
- 前端：Bootstrap 5、JavaScript
- 数据库：SQLite（可配置为其他数据库）
- AI模型：OpenAI API（可配置不同模型）

## 安装与配置

### 环境要求
- Python 3.8+
- pip 或 conda 包管理器

### 安装步骤

1. 克隆仓库
```bash
git clone https://github.com/yourusername/invoice-downloader.git
cd invoice-downloader
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

3. 配置环境变量
创建 `.env` 文件，包含以下配置：
```
SECRET_KEY=your-secret-key-please-change-this-in-production
DATABASE_URI=sqlite:///app.db
OPENAI_API_KEY=your-openai-api-key
OPENAI_API_BASE=http://your-api-base-url
OPENAI_MODEL=gpt-4o
APP_PORT=5001
APP_HOST=0.0.0.0
```

4. 初始化数据库
```bash
flask db init
flask db migrate
flask db upgrade
```

### 使用Conda环境（可选）

1. 创建Conda环境
```bash
conda create -n invoice-env python=3.10
conda activate invoice-env
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

## 使用方法

1. 启动应用
```bash
python app.py
```

2. 在浏览器中访问 `http://localhost:5001`

3. 注册账号并登录

4. 基本使用流程：
   - 添加常用邮箱账号（可选）
   - 导入发票：输入邮箱账号和密码，选择日期范围
   - 等待处理完成，下载处理后的文件
   - 在发票管理页面查看和管理所有发票

## 注意事项

- 对于QQ邮箱、163邮箱等，需要使用授权码而非登录密码
- 首次使用时，请确保邮箱设置允许IMAP访问
- 处理大量发票可能需要较长时间，请耐心等待
- 建议定期备份数据库文件

## 隐私与安全

- 所有密码和敏感信息均在本地处理，不会上传到第三方服务器
- 发票信息提取使用AI模型，可配置为本地模型或OpenAI API
- 用户数据相互隔离，确保数据安全

## 许可证

[MIT License](LICENSE)
