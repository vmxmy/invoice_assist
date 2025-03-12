#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
日期格式迁移脚本
将数据库中的发票日期字段统一转换为标准格式 (YYYY-MM-DD)
"""

import os
import re
from datetime import datetime
from app import app
from models import db, Invoice

def convert_date_format(date_str):
    """
    将各种格式的日期字符串转换为标准的 YYYY-MM-DD 格式
    支持的格式:
    - YYYY年MM月DD日
    - YYYY-MM-DD
    - YYYY/MM/DD
    """
    if not date_str:
        return None
    
    # 尝试匹配中文日期格式: YYYY年MM月DD日
    chinese_pattern = r'(\d{4})年(\d{1,2})月(\d{1,2})日'
    match = re.search(chinese_pattern, date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    
    # 尝试匹配标准日期格式: YYYY-MM-DD
    std_pattern = r'(\d{4})-(\d{1,2})-(\d{1,2})'
    match = re.search(std_pattern, date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    
    # 尝试匹配斜杠日期格式: YYYY/MM/DD
    slash_pattern = r'(\d{4})/(\d{1,2})/(\d{1,2})'
    match = re.search(slash_pattern, date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    
    # 如果无法识别格式，返回原始字符串
    print(f"无法识别的日期格式: {date_str}")
    return date_str

def migrate_invoice_dates():
    """
    迁移发票日期字段
    """
    print("开始迁移发票日期字段...")
    
    with app.app_context():
        # 获取所有发票记录
        invoices = Invoice.query.all()
        print(f"找到 {len(invoices)} 条发票记录")
        
        updated_count = 0
        error_count = 0
        
        for invoice in invoices:
            try:
                # 获取原始日期字符串
                original_date = invoice.invoice_date
                
                # 转换为标准格式
                standardized_date = convert_date_format(original_date)
                
                # 如果转换成功且格式发生变化，则更新数据库
                if standardized_date and standardized_date != original_date:
                    print(f"发票 {invoice.id}: {original_date} -> {standardized_date}")
                    invoice.invoice_date = standardized_date
                    updated_count += 1
            except Exception as e:
                print(f"处理发票 {invoice.id} 时出错: {str(e)}")
                error_count += 1
        
        # 提交更改
        if updated_count > 0:
            db.session.commit()
            print(f"成功更新 {updated_count} 条记录")
        
        if error_count > 0:
            print(f"处理过程中有 {error_count} 条记录出错")
        
        print("日期格式迁移完成")

def update_filename_with_new_date():
    """
    根据新的日期格式更新文件名
    """
    print("开始更新发票文件名...")
    
    with app.app_context():
        # 获取所有发票记录
        invoices = Invoice.query.all()
        print(f"找到 {len(invoices)} 条发票记录")
        
        updated_count = 0
        error_count = 0
        
        for invoice in invoices:
            try:
                # 获取当前信息
                invoice_date = invoice.invoice_date
                seller = invoice.seller[:20].replace('/', '_').replace('\\', '_')
                amount = invoice.amount
                invoice_no = invoice.invoice_no
                
                # 构建新文件名
                new_filename = f"[{invoice_date}-{seller}-{amount}-{invoice_no}].pdf"
                new_filename = new_filename.replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                
                # 如果文件名发生变化，则更新
                if new_filename != invoice.current_filename:
                    print(f"发票 {invoice.id} 文件名更新: {invoice.current_filename} -> {new_filename}")
                    invoice.current_filename = new_filename
                    updated_count += 1
            except Exception as e:
                print(f"更新发票 {invoice.id} 文件名时出错: {str(e)}")
                error_count += 1
        
        # 提交更改
        if updated_count > 0:
            db.session.commit()
            print(f"成功更新 {updated_count} 条记录的文件名")
        
        if error_count > 0:
            print(f"处理过程中有 {error_count} 条记录出错")
        
        print("文件名更新完成")

def update_invoice_date_std_field():
    """
    更新标准日期字段
    """
    print("开始更新标准日期字段...")
    
    with app.app_context():
        # 获取所有发票记录
        invoices = Invoice.query.all()
        print(f"找到 {len(invoices)} 条发票记录")
        
        updated_count = 0
        error_count = 0
        
        for invoice in invoices:
            try:
                # 获取原始日期字符串
                original_date = invoice.invoice_date
                
                # 尝试解析不同格式的日期
                date_obj = None
                
                # 尝试匹配中文日期格式: YYYY年MM月DD日
                chinese_pattern = r'(\d{4})年(\d{1,2})月(\d{1,2})日'
                match = re.search(chinese_pattern, original_date)
                if match:
                    year, month, day = match.groups()
                    try:
                        date_obj = datetime(int(year), int(month), int(day)).date()
                    except ValueError:
                        pass
                
                # 尝试匹配标准日期格式: YYYY-MM-DD
                if not date_obj:
                    std_pattern = r'(\d{4})-(\d{1,2})-(\d{1,2})'
                    match = re.search(std_pattern, original_date)
                    if match:
                        year, month, day = match.groups()
                        try:
                            date_obj = datetime(int(year), int(month), int(day)).date()
                        except ValueError:
                            pass
                
                # 尝试匹配斜杠日期格式: YYYY/MM/DD
                if not date_obj:
                    slash_pattern = r'(\d{4})/(\d{1,2})/(\d{1,2})'
                    match = re.search(slash_pattern, original_date)
                    if match:
                        year, month, day = match.groups()
                        try:
                            date_obj = datetime(int(year), int(month), int(day)).date()
                        except ValueError:
                            pass
                
                # 更新标准日期字段
                if date_obj:
                    print(f"发票 {invoice.id}: {original_date} -> {date_obj}")
                    invoice.invoice_date_std = date_obj
                    updated_count += 1
                else:
                    print(f"发票 {invoice.id}: 无法解析日期 '{original_date}'")
                    error_count += 1
            except Exception as e:
                print(f"处理发票 {invoice.id} 时出错: {str(e)}")
                error_count += 1
        
        # 提交更改
        if updated_count > 0:
            db.session.commit()
            print(f"成功更新 {updated_count} 条记录的标准日期字段")
        
        if error_count > 0:
            print(f"处理过程中有 {error_count} 条记录出错")
        
        print("标准日期字段更新完成")

if __name__ == "__main__":
    print("=== 开始日期格式迁移 ===")
    migrate_invoice_dates()
    print("\n=== 开始更新标准日期字段 ===")
    update_invoice_date_std_field()
    print("\n=== 开始更新文件名 ===")
    update_filename_with_new_date()
    print("\n迁移完成！") 