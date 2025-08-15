from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from ..models.user import User
from ..models.user import Role
from .. import db
from ..services.sso_service import get_sso_service
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # 验证输入
        if not username or not password:
            flash('请填写所有必填字段')
            return redirect(url_for('auth.register'))
        
        # 验证用户名长度
        if len(username) < 3 or len(username) > 20:
            flash('用户名长度必须在3-20个字符之间')
            return redirect(url_for('auth.register'))
            
        # 验证密码长度
        if len(password) < 6:
            flash('密码长度必须大于6个字符')
            return redirect(url_for('auth.register'))
        
        # 检查用户是否已存在
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('auth.register'))
        
        try:
            # 创建新用户
            user = User(username=username)
            user.set_password(password)
            
            # 设置默认用户角色
            default_role = Role.query.filter_by(name='user').first()
            if default_role:
                user.role = default_role
            
            db.session.add(user)
            db.session.commit()
            
            flash('注册成功！请登录')
            return redirect(url_for('auth.login'))
            
        except Exception as e:
            db.session.rollback()
            flash('注册失败，请重试')
            print(f"Registration error: {str(e)}")
            return redirect(url_for('auth.register'))
    
    return render_template('auth/register.html')

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form['password']
        email = request.form.get('email') # 从前端获取email，但非SSO用户登录时可能不需要

        if not username:
            flash('请输入用户名')
            return redirect(url_for('auth.login'))
        
        user = User.query.filter_by(username=username).first()
        
        # 如果用户存在且是SSO用户，则需要验证邮箱
        if user and user.is_sso_user():
            if not email or user.email != email:
                flash('SSO用户登录需要提供正确的邮箱')
                return redirect(url_for('auth.login'))
        # 如果用户存在但不是SSO用户，则不需要验证邮箱
        elif user and not user.is_sso_user():
            pass # 不需要邮箱验证
        # 如果用户不存在，则直接返回用户名或密码错误
        else:
            flash('用户名或密码错误')
            return redirect(url_for('auth.login'))
        if user and user.check_password(password):
            if user.status == 'pending':
                flash('您的账号正在等待管理员审批')
                return redirect(url_for('auth.login'))
            elif user.status == 'rejected':
                flash('您的注册申请已被拒绝')
                return redirect(url_for('auth.login'))
            elif user.status == 'disabled':
                flash('您的账号已被禁用，请联系管理员')
                return redirect(url_for('auth.login'))
            
            login_user(user)
            # 设置 session
            session['username'] = user.username
            session.permanent = True  # 启用永久 session

            # 更新最后登录时间
            from datetime import datetime
            import pytz
            user.last_login = datetime.now(pytz.timezone('Asia/Shanghai'))
            db.session.commit()

            flash('登录成功！')
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('main.index'))
        else:
            flash('用户名或密码错误')
            
    # 获取SSO状态
    try:
        sso_service = get_sso_service()
        sso_enabled = sso_service.is_enabled()
        sso_provider = current_app.config.get('SSO_PROVIDER', 'oauth2')
    except Exception:
        sso_enabled = False
        sso_provider = 'oauth2'

    return render_template('auth/login.html',
                         sso_enabled=sso_enabled,
                         sso_provider=sso_provider)

@bp.route('/logout')
@login_required
def logout():
    # 检查是否为SSO用户
    if current_user.is_sso_user():
        # 重定向到SSO登出
        return redirect(url_for('sso.sso_logout'))

    logout_user()
    session.clear() # Clear all session data
    flash('已退出登录')
    return redirect(url_for('auth.login'))


@bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """修改密码API"""
    try:
        # 检查用户是否可以修改密码
        if not current_user.can_change_password():
            return jsonify({
                'success': False,
                'message': 'SSO用户无法修改密码'
            }), 400

        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': '请求数据格式错误'
            }), 400

        current_password = data.get('current_password')
        new_password = data.get('new_password')

        if not current_password or not new_password:
            return jsonify({
                'success': False,
                'message': '当前密码和新密码不能为空'
            }), 400

        # 验证当前密码
        if not current_user.check_password(current_password):
            return jsonify({
                'success': False,
                'message': '当前密码错误'
            }), 400

        # 验证新密码长度
        if len(new_password) < 6:
            return jsonify({
                'success': False,
                'message': '新密码长度至少为6位'
            }), 400

        # 更新密码
        current_user.set_password(new_password)
        db.session.commit()

        logger.info(f"用户 {current_user.username} 修改密码成功")

        return jsonify({
            'success': True,
            'message': '密码修改成功'
        })

    except Exception as e:
        logger.error(f"修改密码失败: {e}")
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': '密码修改过程中发生错误'
        }), 500


@bp.route('/user-info')
@login_required
def user_info():
    """获取当前用户信息API"""
    try:
        user_data = {
            'id': current_user.id,
            'username': current_user.username,
            'email': current_user.email,
            'display_name': current_user.get_display_name(),
            'full_name': current_user.get_full_name(),
            'role': current_user.role.name if current_user.role else None,
            'is_sso_user': current_user.is_sso_user(),
            'sso_provider': current_user.sso_provider,
            'is_administrator': current_user.is_administrator(),
            'can_change_password': current_user.can_change_password(),
            'last_login': current_user.last_login.isoformat() if current_user.last_login else None,
            'register_time': current_user.register_time.isoformat() if current_user.register_time else None,
            'status': current_user.status
        }

        return jsonify({
            'success': True,
            'data': user_data
        })

    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        return jsonify({
            'success': False,
            'message': '获取用户信息失败'
        }), 500