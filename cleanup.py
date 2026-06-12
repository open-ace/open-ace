#!/usr/bin/env python
"""清理测试数据"""

from app import create_app
from app.repositories.database import get_db_connection

app = create_app()
with app.app_context():
    with get_db_connection() as conn:
        cur = conn.cursor()
        # 删除测试数据
        cur.execute('DELETE FROM compliance_reports WHERE report_id = %s', ('test_report_001',))
        conn.commit()
        print('测试数据已清理')

# 清理临时文件
import os
temp_files = ['verify_fix.py', 'test_fix.py', 'check_db.py', 'create_table.py']
for file in temp_files:
    if os.path.exists(file):
        os.remove(file)
        print(f'已删除临时文件: {file}')