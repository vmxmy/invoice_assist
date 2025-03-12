import imaplib
import email
import os
from email.header import decode_header
from datetime import datetime
import email.utils
import re

def connect_to_email(email_address, password):
    """连接到邮箱服务器"""
    try:
        # 连接到 QQ 邮箱的 IMAP 服务器
        imap_server = "imap.qq.com"
        imap = imaplib.IMAP4_SSL(imap_server)
        imap.login(email_address, password)
        return imap
    except Exception as e:
        print(f"连接邮箱失败: {str(e)}")
        return None

def download_invoice_attachments(imap, date_since=None):
    """下载包含'发票'的邮件中的PDF附件"""
    try:
        # 选择收件箱
        imap.select('INBOX')
        
        # 搜索标题包含"发票"的邮件 - 使用UTF-8编码
        if date_since:
            # 将日期转换为IMAP搜索格式 (DD-MMM-YYYY)
            # 注意：QQ邮箱的IMAP服务器可能不完全支持SINCE命令，所以我们会在客户端再次过滤
            date_str = date_since.strftime("%d-%b-%Y")
            search_criteria = f'(SUBJECT "发票" SINCE "{date_str}")'.encode('utf-8')
            print(f"搜索条件: {search_criteria}")
        else:
            search_criteria = 'SUBJECT "发票"'.encode('utf-8')
            
        _, messages = imap.search(None, search_criteria)
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        # 清空下载目录，避免重复文件
        for file in os.listdir('downloads'):
            file_path = os.path.join('downloads', file)
            if os.path.isfile(file_path):
                os.remove(file_path)
                
        downloaded_count = 0
        skipped_count = 0
        
        print(f"找到 {len(messages[0].split())} 封可能包含发票的邮件")
        
        for msg_num in messages[0].split():
            # 获取邮件内容
            _, msg_data = imap.fetch(msg_num, '(RFC822)')
            email_body = msg_data[0][1]
            email_message = email.message_from_bytes(email_body)
            
            # 获取邮件主题
            subject = decode_header(email_message["subject"])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode('utf-8', errors='ignore')
            
            # 如果指定了日期，严格检查邮件日期
            if date_since:
                date_str = email_message.get('Date')
                if date_str:
                    try:
                        # 解析邮件日期
                        email_date = email.utils.parsedate_to_datetime(date_str)
                        
                        # 打印日期信息以便调试
                        print(f"邮件: {subject}")
                        print(f"邮件日期: {email_date.date()}, 过滤日期: {date_since.date()}")
                        
                        # 如果邮件日期早于指定日期，跳过此邮件
                        if email_date.date() < date_since.date():
                            print(f"跳过邮件 (日期过早): {subject}")
                            skipped_count += 1
                            continue
                    except Exception as e:
                        print(f"日期解析错误: {e}, 邮件: {subject}, 日期字符串: {date_str}")
                        # 如果日期解析失败，继续处理
                        pass
                else:
                    print(f"邮件没有日期信息: {subject}")
                
            print(f"处理邮件: {subject}")
            
            # 处理附件
            has_pdf = False
            for part in email_message.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue
                    
                filename = part.get_filename()
                if filename:
                    # 解码文件名
                    filename_tuple = decode_header(filename)[0]
                    if isinstance(filename_tuple[0], bytes):
                        try:
                            # 尝试使用指定的编码
                            filename = filename_tuple[0].decode(filename_tuple[1] or 'utf-8')
                        except:
                            # 如果失败，尝试其他编码
                            filename = filename_tuple[0].decode('gbk', errors='ignore')
                    
                    # 确保文件名是合法的
                    filename = "".join(c for c in filename if c.isprintable())
                    
                    # 只下载PDF文件
                    if filename.lower().endswith('.pdf'):
                        has_pdf = True
                        filepath = os.path.join('downloads', filename)
                        
                        # 如果文件已存在，添加序号
                        counter = 1
                        base_name, ext = os.path.splitext(filename)
                        while os.path.exists(filepath):
                            filepath = os.path.join('downloads', f"{base_name}_{counter}{ext}")
                            counter += 1
                            
                        with open(filepath, 'wb') as f:
                            f.write(part.get_payload(decode=True))
                        print(f"已下载: {os.path.basename(filepath)}")
                        downloaded_count += 1
            
            if not has_pdf:
                print(f"邮件没有PDF附件: {subject}")
        
        print(f"总共下载了 {downloaded_count} 个PDF附件，跳过了 {skipped_count} 封日期不符合的邮件")
        return downloaded_count
    except Exception as e:
        print(f"下载附件时出错: {str(e)}")
        raise  # 重新抛出异常，让上层函数处理

def main():
    """主函数"""
    print("欢迎使用发票邮件下载器")
    email_address = "vmxmy@qq.com" 
    password = "jwbcaaedignsbjfb"
    
    # 连接到邮箱
    imap = connect_to_email(email_address, password)
    if imap:
        try:
            # 测试日期过滤
            test_date = datetime.strptime("2023-01-01", "%Y-%m-%d")
            download_invoice_attachments(imap, date_since=test_date)
            print("处理完成！")
        except Exception as e:
            print(f"处理过程中出现错误: {str(e)}")
        finally:
            imap.logout()
    
if __name__ == "__main__":
    main() 