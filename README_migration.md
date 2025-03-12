# 发票日期字段迁移指南

本文档提供了将发票系统中的日期字段从字符串格式转换为标准日期格式的详细步骤。

## 背景

当前系统中的日期筛选功能存在问题，主要原因是数据库中的日期字段以字符串格式存储，格式不统一（有"YYYY年MM月DD日"和"YYYY-MM-DD"等多种格式），导致日期比较和筛选不准确。

为解决这个问题，我们需要：

1. 在数据库模型中添加标准日期字段 `invoice_date_std`
2. 将现有的字符串日期解析为标准日期对象并存储
3. 修改应用程序代码，使用标准日期字段进行筛选

## 迁移步骤

### 1. 确保数据库模型已更新

首先确认 `models.py` 文件中的 `Invoice` 模型已包含 `invoice_date_std` 字段：

```python
class Invoice(db.Model):
    # 其他字段...
    invoice_date = db.Column(db.String(20), nullable=True)  # 原始日期字符串
    invoice_date_std = db.Column(db.Date, nullable=True)    # 标准日期格式
    # 其他字段...
```

### 2. 创建数据库表结构

如果这是首次添加 `invoice_date_std` 字段，需要更新数据库表结构：

```bash
# 启动 Python 交互式环境
python

# 在 Python 环境中执行
from app import app, db
with app.app_context():
    db.create_all()
    print("数据库表结构已更新")
```

### 3. 执行日期迁移脚本

运行提供的迁移脚本，将现有的字符串日期转换为标准日期格式：

```bash
python migrate_dates.py
```

这个脚本会执行以下操作：

1. 将字符串日期（如"2023年01月01日"）标准化为"2023-01-01"格式
2. 解析日期字符串并将其转换为日期对象，存储到 `invoice_date_std` 字段
3. 更新发票文件名，确保文件名中的日期格式一致

### 4. 验证迁移结果

迁移完成后，可以通过以下方式验证结果：

```bash
# 启动 Python 交互式环境
python

# 在 Python 环境中执行
from app import app
from models import Invoice
with app.app_context():
    # 检查有多少发票已成功转换日期
    total = Invoice.query.count()
    converted = Invoice.query.filter(Invoice.invoice_date_std != None).count()
    print(f"总发票数: {total}, 已转换日期的发票数: {converted}")
    
    # 查看几个样本
    samples = Invoice.query.limit(5).all()
    for invoice in samples:
        print(f"ID: {invoice.id}, 原始日期: {invoice.invoice_date}, 标准日期: {invoice.invoice_date_std}")
```

### 5. 故障排除

如果在迁移过程中遇到问题，可以尝试以下解决方法：

1. **数据库连接问题**：确保应用上下文正确初始化，检查数据库连接字符串
2. **日期解析错误**：对于无法自动解析的日期，可以手动更新这些记录
3. **模型关系错误**：如果出现关系定义冲突，检查模型中的 `relationship` 和 `backref` 定义

## 后续步骤

完成迁移后，应用程序已经配置为使用新的标准日期字段进行筛选。以下功能现在应该正常工作：

1. 按日期范围筛选发票
2. 按日期排序发票
3. 导出包含标准日期格式的报表

## 注意事项

1. 迁移脚本会保留原始的字符串日期字段，以确保向后兼容性
2. 如果将来导入新发票，系统会自动解析日期并同时填充两个日期字段
3. 建议定期检查是否有未成功转换的日期记录，并手动更新它们 