import os, sys
sys.path.insert(0, '/opt/jt-doc-tools')
from app.core import auth_db, auth_settings, user_manager, sessions
auth_db.init()          # 全新資料目錄裡還沒有 users 表
from app.core import roles
roles.seed_builtin_roles()
auth_settings.save({'backend': 'local'})
pw = 'qY1_xmJ-ezgOlxIFh40b4-L9'
u = user_manager.get_by_username('jtdt-admin')
if not u:
    user_manager.create_local('jtdt-admin', 'ZAP admin', pw, roles=['admin'])
    u = user_manager.get_by_username('jtdt-admin')
# 用印申請預設不啟用：打開並建一個流程，爬蟲才掃得到啟用狀態的頁面與 API
from app.core import seal_policy, seal_flow
seal_policy.set_enabled(True)
seal_flow.save_flow(None, {'name': 'zap', 'stages': [{'name': 's1', 'rule': 'any', 'candidates': [{'type': 'user', 'key': str(u['id'])}]}]})
tok, _ = sessions.issue(u['id'], remember=True, ip='127.0.0.1', ua='zap')
print(tok)
