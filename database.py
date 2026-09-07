"""
ARS - قاعدة البيانات الكاملة — مطابقة لـ v54
"""
import os, hashlib
from datetime import datetime
from sqlalchemy import (create_engine, Column, Integer, String, DateTime,
                        Boolean, ForeignKey, Text, Table)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()

user_locations_table = Table('user_locations', Base.metadata,
    Column('user_id',    Integer, ForeignKey('users.id')),
    Column('location_id',Integer, ForeignKey('locations.id')))

user_venues_table = Table('user_venues', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id')),
    Column('venue_id',Integer, ForeignKey('venues.id')))

class Role(Base):
    __tablename__ = 'roles'
    id=Column(Integer,primary_key=True); name=Column(String(50),unique=True,nullable=False)
    name_en=Column(String(50)); description=Column(Text); is_default=Column(Boolean,default=False)
    is_active=Column(Boolean,default=True); created_at=Column(DateTime,default=datetime.now)
    users=relationship('User',back_populates='role_ref')
    role_permissions=relationship('RolePermission',back_populates='role',cascade='all, delete-orphan')

class Permission(Base):
    __tablename__ = 'permissions'
    id=Column(Integer,primary_key=True); code=Column(String(50),unique=True,nullable=False)
    name=Column(String(100),nullable=False); name_en=Column(String(100))
    module=Column(String(50)); category=Column(String(50)); description=Column(Text)
    is_active=Column(Boolean,default=True); created_at=Column(DateTime,default=datetime.now)
    role_permissions=relationship('RolePermission',back_populates='permission',cascade='all, delete-orphan')

class RolePermission(Base):
    __tablename__ = 'role_permissions'
    id=Column(Integer,primary_key=True); role_id=Column(Integer,ForeignKey('roles.id'))
    permission_id=Column(Integer,ForeignKey('permissions.id'))
    can_view=Column(Boolean,default=False); can_add=Column(Boolean,default=False)
    can_edit=Column(Boolean,default=False); can_delete=Column(Boolean,default=False)
    can_approve=Column(Boolean,default=False)
    role=relationship('Role',back_populates='role_permissions')
    permission=relationship('Permission',back_populates='role_permissions')

class User(Base):
    __tablename__ = 'users'
    id=Column(Integer,primary_key=True); username=Column(String(50),unique=True,nullable=False)
    email=Column(String(100),unique=True,nullable=True); password_hash=Column(String(200),nullable=False)
    role_id=Column(Integer,ForeignKey('roles.id')); full_name=Column(String(100))
    phone=Column(String(20)); verification_code=Column(String(10)); verification_expiry=Column(DateTime)
    reset_code=Column(String(10)); reset_code_expiry=Column(DateTime)
    is_verified=Column(Boolean,default=False); is_active=Column(Boolean,default=True)
    language=Column(String(10),default='ar'); last_login=Column(DateTime)
    login_count=Column(Integer,default=0); created_at=Column(DateTime,default=datetime.now)
    role_ref=relationship('Role',back_populates='users')
    allowed_locations=relationship('Location',secondary=user_locations_table,back_populates='allowed_users')
    allowed_venues=relationship('Venue',secondary=user_venues_table,back_populates='allowed_users')
    reservations=relationship('Reservation',foreign_keys='Reservation.user_id',back_populates='user')
    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)
    @property
    def role(self): return self.role_ref

class Location(Base):
    __tablename__ = 'locations'
    id=Column(Integer,primary_key=True); name=Column(String(100),nullable=False,unique=True)
    name_en=Column(String(100)); city=Column(String(100)); area=Column(String(100))
    is_active=Column(Boolean,default=True); created_at=Column(DateTime,default=datetime.now)
    venues=relationship('Venue',back_populates='location')
    allowed_users=relationship('User',secondary=user_locations_table,back_populates='allowed_locations')

class Venue(Base):
    __tablename__ = 'venues'
    id=Column(Integer,primary_key=True); name=Column(String(100),nullable=False)
    name_en=Column(String(100)); code=Column(String(20),unique=True,nullable=True)
    location_id=Column(Integer,ForeignKey('locations.id')); capacity=Column(Integer)
    equipment=Column(Text); notes=Column(Text); notes_en=Column(Text)
    is_active=Column(Boolean,default=True); requires_approval=Column(Boolean,default=True)
    created_at=Column(DateTime,default=datetime.now)
    location=relationship('Location',back_populates='venues')
    reservations=relationship('Reservation',back_populates='venue')
    blocked_periods=relationship('BlockedPeriod',back_populates='venue')
    allowed_users=relationship('User',secondary=user_venues_table,back_populates='allowed_venues')
    ratings=relationship('Rating',back_populates='venue')

class Contact(Base):
    __tablename__ = 'contacts'
    id=Column(Integer,primary_key=True); first_name=Column(String(100),nullable=False)
    last_name=Column(String(100)); email=Column(String(200),nullable=False)
    phone=Column(String(50)); created_by=Column(Integer,ForeignKey('users.id'))
    created_at=Column(DateTime,default=datetime.now)
    creator=relationship('User',foreign_keys=[created_by])

class BookingContact(Base):
    __tablename__='booking_contacts'
    id=Column(Integer,primary_key=True); booking_id=Column(Integer,ForeignKey('reservations.id'),nullable=True)
    contact_id=Column(Integer,ForeignKey('contacts.id'))
    invitation_sent=Column(Boolean,default=False); sent_at=Column(DateTime)
    booking=relationship('Reservation'); contact=relationship('Contact')

class Reservation(Base):
    __tablename__ = 'reservations'
    id=Column(Integer,primary_key=True); booking_number=Column(String(50),unique=True,nullable=False)
    title=Column(String(200),nullable=False); title_en=Column(String(200))
    start_time=Column(DateTime,nullable=False); end_time=Column(DateTime,nullable=False)
    status=Column(String(20),default='pending'); user_id=Column(Integer,ForeignKey('users.id'))
    venue_id=Column(Integer,ForeignKey('venues.id')); approver_id=Column(Integer,ForeignKey('users.id'))
    approval_date=Column(DateTime); approver_notes=Column(Text); requester_notes=Column(Text)
    cancellation_reason=Column(Text); cancelled_by=Column(Integer,ForeignKey('users.id'))
    cancelled_at=Column(DateTime); created_at=Column(DateTime,default=datetime.now)
    user=relationship('User',foreign_keys=[user_id],back_populates='reservations')
    venue=relationship('Venue',back_populates='reservations')
    approver=relationship('User',foreign_keys=[approver_id])
    canceller=relationship('User',foreign_keys=[cancelled_by])
    checklist_items=relationship('ChecklistItem',back_populates='reservation',cascade='all, delete-orphan',foreign_keys='ChecklistItem.reservation_id')
    approvals=relationship('Approval',back_populates='reservation',cascade='all, delete-orphan')
    booking_contacts=relationship('BookingContact',cascade='all, delete-orphan')
    ratings=relationship('Rating',back_populates='reservation')

class Approval(Base):
    __tablename__='approvals'
    id=Column(Integer,primary_key=True); reservation_id=Column(Integer,ForeignKey('reservations.id'))
    approver_id=Column(Integer,ForeignKey('users.id')); status=Column(String(20))
    comments=Column(Text); created_at=Column(DateTime,default=datetime.now)
    reservation=relationship('Reservation',back_populates='approvals')
    approver=relationship('User',foreign_keys=[approver_id])

class Checklist(Base):
    __tablename__='checklists'
    id=Column(Integer,primary_key=True); name=Column(String(200),nullable=False)
    name_en=Column(String(200)); description=Column(Text)
    is_template=Column(Boolean,default=False); is_public=Column(Boolean,default=True)
    created_by_id=Column(Integer,ForeignKey('users.id')); created_at=Column(DateTime,default=datetime.now)
    creator=relationship('User',foreign_keys=[created_by_id])
    items=relationship('ChecklistItem',back_populates='checklist',cascade='all, delete-orphan',
                       foreign_keys='ChecklistItem.checklist_id')

class ChecklistItem(Base):
    __tablename__='checklist_items'
    id=Column(Integer,primary_key=True); checklist_id=Column(Integer,ForeignKey('checklists.id'),nullable=True)
    reservation_id=Column(Integer,ForeignKey('reservations.id'),nullable=True)
    content=Column(String(500),nullable=False); content_en=Column(String(500))
    is_checked=Column(Boolean,default=False); checked_by_id=Column(Integer,ForeignKey('users.id'))
    checked_at=Column(DateTime); due_date=Column(DateTime); priority=Column(Integer,default=0)
    order_index=Column(Integer,default=0)
    checklist=relationship('Checklist',back_populates='items',foreign_keys=[checklist_id])
    reservation=relationship('Reservation',back_populates='checklist_items',foreign_keys=[reservation_id])
    checked_by=relationship('User',foreign_keys=[checked_by_id])

class Rating(Base):
    __tablename__='ratings'
    id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id'))
    venue_id=Column(Integer,ForeignKey('venues.id')); reservation_id=Column(Integer,ForeignKey('reservations.id'))
    rating=Column(Integer); comment=Column(Text); created_at=Column(DateTime,default=datetime.now)
    user=relationship('User'); venue=relationship('Venue',back_populates='ratings')
    reservation=relationship('Reservation',back_populates='ratings')

class BlockedPeriod(Base):
    __tablename__='blocked_periods'
    id=Column(Integer,primary_key=True); venue_id=Column(Integer,ForeignKey('venues.id'))
    location_id=Column(Integer,ForeignKey('locations.id'))
    start_time=Column(DateTime,nullable=False); end_time=Column(DateTime,nullable=False)
    reason=Column(String(500)); reason_en=Column(String(500))
    created_by_id=Column(Integer,ForeignKey('users.id')); created_at=Column(DateTime,default=datetime.now)
    venue=relationship('Venue',back_populates='blocked_periods')
    location=relationship('Location',foreign_keys=[location_id])
    created_by=relationship('User',foreign_keys=[created_by_id])

class EmailLog(Base):
    __tablename__='email_logs'
    id=Column(Integer,primary_key=True); recipient=Column(String(200)); subject=Column(String(500))
    type=Column(String(50)); status=Column(String(20)); error_message=Column(Text)
    sent_at=Column(DateTime,default=datetime.now); user_id=Column(Integer,ForeignKey('users.id'))

class SystemLog(Base):
    __tablename__='system_logs'
    id=Column(Integer,primary_key=True); action=Column(String(100)); description=Column(Text)
    user_id=Column(Integer,ForeignKey('users.id')); level=Column(String(20),default='info')
    created_at=Column(DateTime,default=datetime.now)
    user=relationship('User',foreign_keys=[user_id])

class LoginLog(Base):
    __tablename__='login_logs'
    id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id'),nullable=True)
    username=Column(String(100)); ip_address=Column(String(60)); hostname=Column(String(200))
    platform=Column(String(200)); success=Column(Boolean,default=False); reason=Column(String(200))
    created_at=Column(DateTime,default=datetime.now); login_time=Column(DateTime,default=datetime.now)
    user=relationship('User',foreign_keys=[user_id])

def get_engine():
    db_url = os.environ.get('DATABASE_URL','')
    if db_url.startswith('postgres://'): db_url='postgresql://'+db_url[11:]
    if db_url and db_url.startswith('postgresql'):
        engine=create_engine(db_url,pool_size=10,max_overflow=20,pool_pre_ping=True,pool_recycle=300)
        print('✅ قاعدة البيانات: PostgreSQL')
    else:
        db_path=os.environ.get('DB_PATH','ars_venues.db')
        engine=create_engine(
            f'sqlite:///{db_path}',
            connect_args={
                'check_same_thread': False,
                'timeout': 30,         # wait up to 30s if locked
            },
            poolclass=StaticPool,
            pool_pre_ping=True,
        )
        # Enable WAL mode for better concurrency
        from sqlalchemy import event
        @event.listens_for(engine, 'connect')
        def _set_wal(dbapi_conn, _rec):
            try:
                dbapi_conn.execute('PRAGMA journal_mode=WAL')
                dbapi_conn.execute('PRAGMA synchronous=NORMAL')
                dbapi_conn.execute('PRAGMA busy_timeout=30000')
            except Exception:
                pass
        from sqlalchemy import event
        @event.listens_for(engine,'connect')
        def set_wal(conn,rec):
            conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA cache_size=10000'); conn.execute('PRAGMA foreign_keys=ON')
        print(f'✅ قاعدة البيانات: SQLite WAL ({db_path})')
    Base.metadata.create_all(engine)
    return engine

def get_session():
    engine=get_engine(); Session=sessionmaker(bind=engine,expire_on_commit=False); return Session()

def seed_database(session):
    if session.query(User).count()>0: return
    print('🌱 بذر قاعدة البيانات...')
    perms_data=[
        ('dashboard_view','عرض لوحة التحكم','رئيسي'),('calendar_view','عرض التقويم','رئيسي'),
        ('locations_view','عرض المواقع','مواقع'),('locations_add','إضافة مواقع','مواقع'),
        ('locations_edit','تعديل مواقع','مواقع'),('locations_delete','حذف مواقع','مواقع'),
        ('venues_view','عرض القاعات','قاعات'),('venues_add','إضافة قاعات','قاعات'),
        ('venues_edit','تعديل قاعات','قاعات'),('venues_delete','حذف قاعات','قاعات'),
        ('reservations_view','عرض الحجوزات','حجوزات'),('reservations_add','إضافة حجوزات','حجوزات'),
        ('reservations_edit','تعديل حجوزات','حجوزات'),('reservations_delete','حذف حجوزات','حجوزات'),
        ('reservations_approve','الموافقة على الحجوزات','حجوزات'),
        ('users_view','عرض المستخدمين','مستخدمين'),('users_add','إضافة مستخدمين','مستخدمين'),
        ('users_edit','تعديل مستخدمين','مستخدمين'),('users_delete','حذف مستخدمين','مستخدمين'),
        ('reports_view','عرض التقارير','تقارير'),('reports_export','تصدير التقارير','تقارير'),
        ('checklists_view','عرض قوائم المهام','مهام'),('checklists_add','إضافة قوائم مهام','مهام'),
        ('checklists_edit','تعديل قوائم مهام','مهام'),('checklists_delete','حذف قوائم مهام','مهام'),
        ('contacts_view','عرض جهات الاتصال','اتصال'),('contacts_add','إضافة جهات اتصال','اتصال'),
        ('contacts_edit','تعديل جهات اتصال','اتصال'),
        ('maintenance_access','الوصول للصيانة','إدارة'),('settings_access','الوصول للإعدادات','إدارة'),
    ]
    perm_objs={}
    for code,name,module in perms_data:
        p=Permission(code=code,name=name,module=module,category=module); session.add(p); perm_objs[code]=p
    session.flush()
    admin_role=Role(name='مدير النظام',name_en='Admin',description='كامل الصلاحيات',is_default=True)
    manager_role=Role(name='مشرف',name_en='Manager',description='صلاحيات إشرافية',is_default=True)
    user_role=Role(name='مستخدم',name_en='User',description='صلاحيات أساسية',is_default=True)
    session.add_all([admin_role,manager_role,user_role]); session.flush()
    for p in perm_objs.values():
        session.add(RolePermission(role_id=admin_role.id,permission_id=p.id,can_view=True,can_add=True,can_edit=True,can_delete=True,can_approve=True))
    for p in perm_objs.values():
        session.add(RolePermission(role_id=manager_role.id,permission_id=p.id,can_view=True,can_add='add' in p.code,can_edit='edit' in p.code,can_delete=False,can_approve='approve' in p.code))
    for code in ['dashboard_view','calendar_view','venues_view','reservations_view','reservations_add','checklists_view','reports_view','contacts_view']:
        if code in perm_objs:
            session.add(RolePermission(role_id=user_role.id,permission_id=perm_objs[code].id,can_view=True,can_add=(code=='reservations_add'),can_edit=False,can_delete=False,can_approve=False))
    session.flush()
    def _h(pw): return hashlib.sha256(pw.encode()).hexdigest()
    session.add(User(username='admin',email='admin@ars.local',password_hash=_h('admin'),role_id=admin_role.id,full_name='مدير النظام',is_verified=True,is_active=True))
    session.add(User(username='manager',email='manager@ars.local',password_hash=_h('manager'),role_id=manager_role.id,full_name='المشرف العام',is_verified=True,is_active=True))
    session.add(User(username='user',email='user@ars.local',password_hash=_h('user'),role_id=user_role.id,full_name='مستخدم عادي',is_verified=True,is_active=True))
    # Seed sample locations, venues, reservations, and blocked periods
    from datetime import timedelta
    loc1 = Location(name='المبنى الرئيسي', name_en='Main Building', city='الرياض', area='حي الملك عبدالله', is_active=True)
    loc2 = Location(name='مركز المؤتمرات', name_en='Conference Center', city='الرياض', area='حي السفارات', is_active=True)
    session.add_all([loc1, loc2]); session.flush()

    venue1 = Venue(name='قاعة الاجتماعات الكبرى', code='MH-01', capacity=50, location_id=loc1.id, is_active=True, needs_approval=True)
    venue2 = Venue(name='قاعة التدريب A', code='TR-A', capacity=20, location_id=loc1.id, is_active=True, needs_approval=False)
    venue3 = Venue(name='قاعة المؤتمرات الدولية', code='CC-INT', capacity=200, location_id=loc2.id, is_active=True, needs_approval=True)
    session.add_all([venue1, venue2, venue3]); session.flush()

    admin_user   = session.query(User).filter_by(username='admin').first()
    manager_user = session.query(User).filter_by(username='manager').first()
    regular_user = session.query(User).filter_by(username='user').first()

    now = datetime.now()
    def _res(title, user, venue, days_offset, duration_h=2, status='approved', rtype='official'):
        sd = now + timedelta(days=days_offset)
        sd = sd.replace(hour=9, minute=0, second=0, microsecond=0)
        import random, string
        bn = 'RES-' + ''.join(random.choices(string.digits, k=6))
        return Reservation(booking_number=bn, title=title, user_id=user.id,
                           venue_id=venue.id, start_time=sd,
                           end_time=sd + timedelta(hours=duration_h),
                           status=status, booking_type=rtype,
                           requester_notes='ملاحظة تجريبية')

    reservations = [
        _res('اجتماع مجلس الإدارة', admin_user, venue1, 0, 3),
        _res('دورة تدريبية متقدمة', manager_user, venue2, 1, 4, 'approved'),
        _res('مؤتمر التقنية السنوي', admin_user, venue3, 2, 8, 'approved', 'external'),
        _res('اجتماع فريق التطوير', regular_user, venue2, 3, 2, 'pending'),
        _res('ورشة عمل القيادة', manager_user, venue1, 5, 3, 'approved'),
        _res('اجتماع المستخدمين', regular_user, venue2, -2, 2, 'approved'),
        _res('عرض تقديمي للمشروع', regular_user, venue1, 7, 1, 'pending'),
        _res('لقاء الإدارة العليا', admin_user, venue3, -5, 4, 'completed'),
        _res('تدريب الموظفين الجدد', manager_user, venue2, 10, 5, 'approved'),
        _res('حجز مؤجل', regular_user, venue1, -1, 2, 'rejected'),
    ]
    for r in reservations:
        session.add(r)
    session.flush()

    # Block a period
    bp = BlockedPeriod(
        venue_id=venue3.id,
        start_time=(now + timedelta(days=14)).replace(hour=0,minute=0),
        end_time=(now + timedelta(days=16)).replace(hour=23,minute=59),
        reason='صيانة دورية للقاعة',
        created_by_id=admin_user.id
    )
    session.add(bp)

    session.commit()
    print('✅ تم البذر: admin/admin | manager/manager | user/user')
    print(f'✅ بيانات تجريبية: {len(reservations)} حجز + فترة محظورة + 3 قاعات + موقعان')
