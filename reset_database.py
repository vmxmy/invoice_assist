#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
数据库重置脚本
删除现有数据库并重新初始化一个新的数据库
"""

import os
import sys
from app import app, db
from models import User, EmailAccount, InvoiceHistory, Invoice

def reset_database():
    """
    删除现有数据库并重新创建
    """
    with app.app_context():
        # 获取数据库URI
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        print(f"当前数据库URI: {db_uri}")
        
        # 如果是SQLite数据库，直接删除文件
        if db_uri.startswith('sqlite:///'):
            db_path = db_uri.replace('sqlite:///', '')
            if os.path.exists(db_path):
                print(f"删除现有数据库文件: {db_path}")
                os.remove(db_path)
                print("数据库文件已删除")
            else:
                print(f"数据库文件不存在: {db_path}")
        else:
            # 对于其他数据库，需要删除所有表并重新创建
            print("非SQLite数据库，将删除所有表并重新创建")
            db.drop_all()
            print("所有表已删除")
        
        # 创建新的数据库表
        db.create_all()
        print("新的数据库表已创建")
        
        # 检查是否需要创建管理员账户
        create_admin = input("是否创建管理员账户? (y/n): ").strip().lower()
        if create_admin == 'y':
            username = input("请输入管理员用户名: ").strip()
            email = input("请输入管理员邮箱: ").strip()
            password = input("请输入管理员密码: ").strip()
            
            admin = User(username=username, email=email)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            print(f"管理员账户已创建: {username}")
        
        print("数据库重置完成")

if __name__ == "__main__":
    # 确认操作
    print("警告: 此操作将删除所有现有数据，且无法恢复!")
    confirm = input("确定要继续吗? 输入 'YES' 确认: ").strip()
    
    if confirm == 'YES':
        reset_database()
    else:
        print("操作已取消")
        sys.exit(0) 