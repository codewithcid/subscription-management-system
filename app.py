import calendar
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from urllib.parse import urlparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
DB = os.getenv('CADENCE_DB', str(ROOT / 'cadence.db'))
DATABASE_URL=os.getenv('DATABASE_URL','')
PUBLIC_DEMO=os.getenv('CADENCE_PUBLIC_DEMO')=='1'
app = FastAPI(title='Cadence', docs_url='/api/docs')
attempts=defaultdict(deque)

@app.middleware('http')
async def security_headers(request,call_next):
    if request.method=='POST':
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'Cross-origin requests are not allowed.'},status_code=403)
        if int(request.headers.get('content-length','0') or 0)>100000:
            return JSONResponse({'detail':'Request too large.'},status_code=413)
        if request.url.path in ('/api/login','/api/signup','/api/reset-request'):
            key=(request.client.host if request.client else 'unknown',request.url.path)
            bucket=attempts[key]; moment=time.monotonic()
            while bucket and bucket[0]<moment-60: bucket.popleft()
            if len(bucket)>=30: return JSONResponse({'detail':'Too many attempts. Please wait one minute.'},status_code=429)
            bucket.append(moment)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(ValueError)
@app.exception_handler(KeyError)
async def invalid_input(request, exc):
    return JSONResponse({'detail':'Some fields are missing or invalid. Please check the form.'},status_code=422)

@contextmanager
def database():
    if DATABASE_URL:
        from storage import Postgres
        con=Postgres(DATABASE_URL)
    else:
        con = sqlite3.connect(DB, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def today(): return date.today()
def now(): return datetime.now(timezone.utc).isoformat()
def fail(message, status=400): raise HTTPException(status, message)
def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
def valid_password(p):
    if len(p) <= 8 or not re.search('[A-Z]', p) or not re.search('[a-z]', p) or not re.search('[^a-zA-Z0-9]', p):
        fail('Use more than 8 characters, including uppercase, lowercase and a special character.')
def cents(v):
    try: return int((Decimal(str(v)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except Exception: fail('Enter a valid amount.')
def row(r): return dict(r) if r else None
def advance(day, interval, anchor=None):
    d = date.fromisoformat(day)
    if interval in ('daily', 'weekly'): return (d + timedelta(days=1 if interval == 'daily' else 7)).isoformat()
    m = d.month + (12 if interval == 'yearly' else 1)
    y, m = d.year + (m-1)//12, (m-1)%12+1
    anchor_day=date.fromisoformat(anchor).day if anchor else d.day
    return date(y, m, min(anchor_day, calendar.monthrange(y,m)[1])).isoformat()
def event(c, sub_id, title, detail):
    c.execute('INSERT INTO events(subscription_id,title,detail,created_at) VALUES(?,?,?,?)', (sub_id,title,detail,now()))

def init_db():
    with database() as c:
        if DATABASE_URL: c.execute('BEGIN IMMEDIATE')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE COLLATE NOCASE, password TEXT, role TEXT CHECK(role IN ('admin','staff','customer')));
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires TEXT);
        CREATE TABLE IF NOT EXISTS resets(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires TEXT);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,name TEXT,type TEXT,price INTEGER,cost INTEGER,notes TEXT,variant TEXT,variant_price INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY,name TEXT,price INTEGER,interval TEXT,min_quantity INTEGER,start_date TEXT,end_date TEXT,auto_close INTEGER,can_pause INTEGER,can_close INTEGER,can_renew INTEGER);
        CREATE TABLE IF NOT EXISTS taxes(id INTEGER PRIMARY KEY,name TEXT,rate INTEGER);
        CREATE TABLE IF NOT EXISTS discounts(id INTEGER PRIMARY KEY,code TEXT UNIQUE,kind TEXT,value INTEGER,min_quantity INTEGER,min_amount INTEGER,start_date TEXT,end_date TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0,product_id INTEGER);
        CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY,name TEXT,plan_id INTEGER REFERENCES plans(id),product_id INTEGER REFERENCES products(id),quantity INTEGER,valid_days INTEGER);
        CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES users(id),plan_id INTEGER REFERENCES plans(id),product_id INTEGER REFERENCES products(id),quantity INTEGER,unit_price INTEGER,tax_rate INTEGER,discount_id INTEGER REFERENCES discounts(id),status TEXT,start_date TEXT,end_date TEXT,next_bill TEXT,payment_terms INTEGER,created_at TEXT);
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY,subscription_id INTEGER REFERENCES subscriptions(id),period TEXT,subtotal INTEGER,discount INTEGER,tax INTEGER,total INTEGER,status TEXT,due_date TEXT,created_at TEXT,UNIQUE(subscription_id,period));
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,invoice_id INTEGER REFERENCES invoices(id),amount INTEGER,method TEXT,date TEXT,note TEXT,idempotency_key TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,subscription_id INTEGER REFERENCES subscriptions(id),title TEXT,detail TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,recipient TEXT,subject TEXT,body TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS subscription_lines(id INTEGER PRIMARY KEY,subscription_id INTEGER REFERENCES subscriptions(id),product_id INTEGER REFERENCES products(id),name TEXT,quantity INTEGER,unit_price INTEGER);
        CREATE TABLE IF NOT EXISTS invoice_lines(id INTEGER PRIMARY KEY,invoice_id INTEGER REFERENCES invoices(id),name TEXT,quantity INTEGER,unit_price INTEGER);
        CREATE INDEX IF NOT EXISTS idx_sub_customer ON subscriptions(customer_id);
        CREATE INDEX IF NOT EXISTS idx_sub_due ON subscriptions(status,next_bill);
        CREATE INDEX IF NOT EXISTS idx_invoice_sub ON invoices(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_payment_invoice ON payments(invoice_id);
        ''')
        if 'quote_expires' not in [r['name'] for r in c.execute('PRAGMA table_info(subscriptions)')]:
            c.execute('ALTER TABLE subscriptions ADD COLUMN quote_expires TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower ON users(LOWER(email))')
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]: return
        hashed = password_hash('CadenceDemo!2026')
        for name,email,role in [('Alex Morgan','admin@cadence.demo','admin'),('Sam Rivera','staff@cadence.demo','staff'),('Olivia Rhye','olivia@acme.demo','customer'),('Phoenix Baker','phoenix@layers.demo','customer'),('Lana Steiner','lana@sisyphus.demo','customer'),('Demi Wilkinson','demi@circooles.demo','customer'),('Drew Cano','drew@catalog.demo','customer'),('Natali Craig','natali@quotient.demo','customer'),('Orlando Diggs','orlando@hourglass.demo','customer'),('Andi Lane','andi@command.demo','customer')]:
            c.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',(name,email,hashed,role))
        for name,price,cost,notes in [('Workspace',4900,1200,'Your everyday operating system.'),('Automations',2900,500,'Put repetitive work on autopilot.'),('Intelligence',9900,2500,'A clearer view of your business.')]:
            c.execute('INSERT INTO products(name,type,price,cost,notes,variant,variant_price) VALUES(?,?,?,?,?,?,?)',(name,'Software',price,cost,notes,'Odoo',56000))
        for name,price,interval,minq in [('Starter',4900,'monthly',1),('Growth',12900,'monthly',1),('Scale',24900,'monthly',3),('Annual',129000,'yearly',1)]:
            c.execute('INSERT INTO plans(name,price,interval,min_quantity,start_date,end_date,auto_close,can_pause,can_close,can_renew) VALUES(?,?,?,?,?,?,1,1,1,1)',(name,price,interval,minq,(today()-timedelta(days=180)).isoformat(),(today()+timedelta(days=730)).isoformat()))
        c.execute('INSERT INTO taxes(name,rate) VALUES(?,?)',('GST',1800))
        c.execute('INSERT INTO discounts(code,kind,value,min_quantity,min_amount,start_date,end_date,max_uses) VALUES(?,?,?,?,?,?,?,?)',('WELCOME20','percentage',2000,1,0,(today()-timedelta(days=30)).isoformat(),(today()+timedelta(days=90)).isoformat(),100))
        c.execute("INSERT INTO templates(name,plan_id,product_id,quantity,valid_days) VALUES('Growth launch',2,1,5,30)")
        for i in range(8):
            plan = (i%4)+1
            price = [4900,12900,24900,129000][plan-1]
            qty = [8,12,5,2,4,8,3,5][i]
            status = ['Active','Active','Active','Active','Active','Paused','Quotation','Active'][i]
            due = (today()+timedelta(days=[0,2,0,6,12,3,0,0][i])).isoformat()
            sid = c.execute('INSERT INTO subscriptions(customer_id,plan_id,product_id,quantity,unit_price,tax_rate,discount_id,status,start_date,end_date,next_bill,payment_terms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(i+3,plan,1,qty,price,1800,1 if i==0 else None,status,(today()-timedelta(days=90)).isoformat(),(today()+timedelta(days=275)).isoformat(),due,14,now())).lastrowid
            event(c,sid,'Subscription created','Imported into your demo workspace')
            if status == 'Active':
                event(c,sid,'Subscription activated','Recurring billing is ready')
                for months in range(5,0,-1):
                    period=(today()-timedelta(days=30*months)).isoformat()
                    sub=price*qty; tax=round(sub*.18)
                    paid = not (i in (2,4) and months == 1)
                    inv=c.execute('INSERT INTO invoices(subscription_id,period,subtotal,discount,tax,total,status,due_date,created_at) VALUES(?,?,?,0,?,?,?,?,?)',(sid,period,sub,tax,sub+tax,'Paid' if paid else 'Confirmed',(date.fromisoformat(period)+timedelta(days=14)).isoformat(),period+'T09:00:00+00:00')).lastrowid
                    if paid: c.execute('INSERT INTO payments(invoice_id,amount,method,date,note,idempotency_key) VALUES(?,?,?,?,?,?)',(inv,sub+tax,'Bank transfer',period,'Demo payment',f'seed-{inv}'))

init_db()

@app.get('/api/health')
def health():
    with database() as c: c.execute('SELECT 1').fetchone()
    return {'status':'ok','storage':'postgresql' if DATABASE_URL else 'sqlite','demo':PUBLIC_DEMO}

def user(request, roles=None):
    with database() as c:
        u=row(c.execute('SELECT u.id,u.name,u.email,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(request.cookies.get('cadence_session',''),now())).fetchone())
    if not u: fail('Please sign in to continue.',401)
    if roles and u['role'] not in roles: fail('You do not have permission for this action.',403)
    return u

def owned(c, sid, u):
    s=row(c.execute('SELECT * FROM subscriptions WHERE id=?',(sid,)).fetchone())
    if not s: fail('Subscription not found.',404)
    if u['role']=='customer' and s['customer_id']!=u['id']: fail('Subscription not found.',404)
    return s

@app.post('/api/login')
async def login(request:Request,response:Response):
    b=await request.json()
    with database() as c:
        u=row(c.execute('SELECT * FROM users WHERE LOWER(email)=LOWER(?)',(b.get('email','').strip(),)).fetchone())
        if not u or not hmac.compare_digest(u['password'],password_hash(b.get('password',''),u['password'].split(':')[0])): fail('Email or password is incorrect.',401)
        token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,u['id'],(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()))
        response.set_cookie('cadence_session',token,httponly=True,samesite='strict',secure=os.getenv('CADENCE_SECURE_COOKIE')=='1',max_age=43200)
    return {'ok':True}

@app.post('/api/logout')
def logout(request:Request,response:Response):
    with database() as c: c.execute('DELETE FROM sessions WHERE token=?',(request.cookies.get('cadence_session',''),))
    response.delete_cookie('cadence_session'); return {'ok':True}

@app.post('/api/signup')
async def signup(request:Request):
    if PUBLIC_DEMO: fail('Public demo uses synthetic accounts. Please use a demo role instead.',403)
    b=await request.json(); valid_password(b.get('password',''))
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',b.get('email','')) or not b.get('name','').strip(): fail('Enter a name and valid email.')
    with database() as c:
        try: c.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',(b['name'].strip(),b['email'].strip().lower(),password_hash(b['password']),'customer'))
        except sqlite3.IntegrityError: fail('This email already has an account.',409)
    return {'ok':True}

@app.post('/api/reset-request')
async def reset_request(request:Request):
    if PUBLIC_DEMO: fail('Password changes are disabled for shared demo accounts.',403)
    b=await request.json()
    with database() as c:
        u=c.execute('SELECT id,email FROM users WHERE email=?',(b.get('email',''),)).fetchone()
        if u:
            token=secrets.token_urlsafe(32)
            c.execute('INSERT INTO resets VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()))
            c.execute('INSERT INTO outbox(recipient,subject,body,created_at) VALUES(?,?,?,?)',(u['email'],'Reset your Cadence password',f'/reset?token={token}',now()))
    return {'message':'If the account exists, a reset link has been queued. Local demo mail is available to the admin in Settings.'}

@app.post('/api/reset')
async def reset(request:Request):
    if PUBLIC_DEMO: fail('Password changes are disabled for shared demo accounts.',403)
    b=await request.json(); valid_password(b.get('password',''))
    token=hashlib.sha256(b.get('token','').encode()).hexdigest()
    with database() as c:
        r=c.execute('SELECT * FROM resets WHERE token=? AND expires>?',(token,now())).fetchone()
        if not r: fail('This reset link has expired or was already used.')
        c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(b['password']),r['user_id']))
        c.execute('DELETE FROM resets WHERE user_id=?',(r['user_id'],)); c.execute('DELETE FROM sessions WHERE user_id=?',(r['user_id'],))
    return {'ok':True}

@app.get('/api/workspace')
def workspace(request:Request):
    u=user(request)
    with database() as c:
        where=' WHERE s.customer_id=?' if u['role']=='customer' else ''
        args=(u['id'],) if where else ()
        subs=[dict(r) for r in c.execute('SELECT s.*,u.name customer,u.email,p.name plan,p.interval,pr.name product FROM subscriptions s JOIN users u ON u.id=s.customer_id JOIN plans p ON p.id=s.plan_id JOIN products pr ON pr.id=s.product_id'+where+' ORDER BY s.id DESC',args)]
        allowed={s['id'] for s in subs}
        line_map={}
        for r in c.execute('SELECT * FROM subscription_lines'):
            if r['subscription_id'] in allowed: line_map.setdefault(r['subscription_id'],[]).append(dict(r))
        for s in subs:
            s['lines']=line_map.get(s['id'],[{'product_id':s['product_id'],'name':s['product'],'quantity':s['quantity'],'unit_price':s['unit_price']}])
            s['recurring_total']=sum(l['quantity']*l['unit_price'] for l in s['lines'])
        invs=[dict(r) for r in c.execute('SELECT i.*,u.name customer,COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) paid FROM invoices i JOIN subscriptions s ON s.id=i.subscription_id JOIN users u ON u.id=s.customer_id'+where+' ORDER BY i.id DESC',args)]
        invoice_ids={i['id'] for i in invs}; invoice_line_map={}
        for r in c.execute('SELECT * FROM invoice_lines'):
            if r['invoice_id'] in invoice_ids: invoice_line_map.setdefault(r['invoice_id'],[]).append(dict(r))
        subscription_map={s['id']:s for s in subs}
        for i in invs: i['lines']=invoice_line_map.get(i['id'],subscription_map[i['subscription_id']]['lines'])
        events=[dict(r) for r in c.execute('SELECT e.* FROM events e JOIN subscriptions s ON s.id=e.subscription_id'+where+' ORDER BY e.id DESC LIMIT 100',args)]
        payments=[dict(r) for r in c.execute('SELECT p.* FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN subscriptions s ON s.id=i.subscription_id'+where+' ORDER BY p.id DESC',args)]
        data={'user':u,'subscriptions':subs,'invoices':invs,'events':events,'payments':payments,'today':today().isoformat(),'public_demo':PUBLIC_DEMO,'storage':'PostgreSQL' if DATABASE_URL else 'SQLite'}
        for table in ('products','plans','taxes','templates'):
            data[table]=[dict(r) for r in c.execute('SELECT * FROM '+table)]
        if u['role']=='customer':
            for product in data['products']: product.pop('cost',None)
            data['templates']=[]
        if u['role']!='customer':
            data['customers']=[dict(r) for r in c.execute("SELECT id,name,email,role FROM users WHERE role='customer'")]
            data['discounts']=[dict(r) for r in c.execute('SELECT * FROM discounts')]
        if u['role']=='admin':
            data['team']=[dict(r) for r in c.execute("SELECT id,name,email,role FROM users WHERE role!='customer'")]
            data['outbox']=[dict(r) for r in c.execute('SELECT * FROM outbox ORDER BY id DESC LIMIT 30')]
    return data

@app.post('/api/catalog/{kind}')
async def catalog(kind:str,request:Request):
    user(request,['admin']); b=await request.json()
    with database() as c:
        if kind=='products':
            if not b.get('name') or cents(b.get('price',0))<0: fail('Provide a product name and non-negative price.')
            q='INSERT INTO products(name,type,price,cost,notes,variant,variant_price) VALUES(?,?,?,?,?,?,?)'; vals=(b['name'],b.get('type','Software'),cents(b['price']),cents(b.get('cost',0)),b.get('notes',''),b.get('variant',''),cents(b.get('variant_price',0)))
        elif kind=='plans':
            if b.get('interval') not in ('daily','weekly','monthly','yearly') or cents(b.get('price',0))<0 or int(b.get('min_quantity',1))<1: fail('Invalid plan values.')
            if b['end_date']<b['start_date']: fail('End date must follow start date.')
            q='INSERT INTO plans(name,price,interval,min_quantity,start_date,end_date,auto_close,can_pause,can_close,can_renew) VALUES(?,?,?,?,?,?,?,?,?,?)'; vals=(b['name'],cents(b['price']),b['interval'],int(b.get('min_quantity',1)),b['start_date'],b['end_date'],int(b.get('auto_close',True)),int(b.get('can_pause',True)),int(b.get('can_close',True)),int(b.get('can_renew',True)))
        elif kind=='taxes':
            rate=cents(b['rate'])
            if not 0<=rate<=10000: fail('Tax must be between 0 and 100 percent.')
            q='INSERT INTO taxes(name,rate) VALUES(?,?)'; vals=(b['name'],rate)
        elif kind=='discounts':
            val=cents(b['value'])
            if b['kind'] not in ('fixed','percentage') or val<0 or (b['kind']=='percentage' and val>10000): fail('Invalid discount.')
            if b['end_date']<b['start_date'] or int(b['max_uses'])<1: fail('Invalid dates or usage limit.')
            q='INSERT INTO discounts(code,kind,value,min_quantity,min_amount,start_date,end_date,max_uses,product_id) VALUES(?,?,?,?,?,?,?,?,?)'; vals=(b['code'].upper(),b['kind'],val,int(b.get('min_quantity',1)),cents(b.get('min_amount',0)),b['start_date'],b['end_date'],int(b['max_uses']),int(b['product_id']) if b.get('product_id') else None)
        elif kind=='templates':
            if int(b['quantity'])<1 or int(b['valid_days'])<1: fail('Quantity and quote validity must be positive.')
            q='INSERT INTO templates(name,plan_id,product_id,quantity,valid_days) VALUES(?,?,?,?,?)'; vals=(b['name'],int(b['plan_id']),int(b['product_id']),int(b['quantity']),int(b['valid_days']))
        elif kind=='staff':
            if PUBLIC_DEMO: fail('Staff creation is disabled in the public demo. Use the Staff demo role.',403)
            valid_password(b['password'])
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',b.get('email','')): fail('Enter a valid email.')
            q="INSERT INTO users(name,email,password,role) VALUES(?,?,?,'staff')"; vals=(b['name'],b['email'],password_hash(b['password']))
        else: fail('Unknown resource.',404)
        try: rid=c.execute(q,vals).lastrowid
        except sqlite3.IntegrityError: fail('This entry already exists or references an invalid record.',409)
    return {'id':rid}

@app.post('/api/subscriptions')
async def create_subscription(request:Request):
    u=user(request); b=await request.json()
    with database() as c:
        p=row(c.execute('SELECT * FROM plans WHERE id=?',(b['plan_id'],)).fetchone()); pr=row(c.execute('SELECT * FROM products WHERE id=?',(b['product_id'],)).fetchone())
        if not p or not pr: fail('Choose an existing plan and product.')
        qty=int(b.get('quantity',1)); start=b.get('start_date',today().isoformat()); end=b.get('end_date',(today()+timedelta(days=365)).isoformat())
        date.fromisoformat(start); date.fromisoformat(end)
        if not 0<=int(b.get('payment_terms',14))<=365: fail('Payment terms must be between 0 and 365 days.')
        if qty<p['min_quantity'] or qty>100000: fail(f"Quantity must be between {p['min_quantity']} and 100000.")
        if not p['start_date']<=start<=p['end_date'] or end<start: fail('Choose valid dates within the plan availability.')
        customer=u['id'] if u['role']=='customer' else int(b['customer_id'])
        if not c.execute("SELECT id FROM users WHERE id=? AND role='customer'",(customer,)).fetchone(): fail('Choose a customer.')
        tax=c.execute('SELECT rate FROM taxes WHERE id=?',(b.get('tax_id',1),)).fetchone()
        discount=int(b['discount_id']) if b.get('discount_id') and u['role']!='customer' else None
        price=p['price']+(pr['variant_price'] if b.get('variant') else 0)
        sid=c.execute('INSERT INTO subscriptions(customer_id,plan_id,product_id,quantity,unit_price,tax_rate,discount_id,status,start_date,end_date,next_bill,payment_terms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(customer,p['id'],pr['id'],qty,price,tax['rate'] if tax else 0,discount,'Draft',start,end,start,int(b.get('payment_terms',14)),now())).lastrowid
        c.execute('INSERT INTO subscription_lines(subscription_id,product_id,name,quantity,unit_price) VALUES(?,?,?,?,?)',(sid,pr['id'],pr['name']+(' · '+pr['variant'] if b.get('variant') else ''),qty,price))
        if len(b.get('lines',[]))>30: fail('Use at most 30 additional product lines.')
        for line in b.get('lines',[]):
            product=row(c.execute('SELECT * FROM products WHERE id=?',(line['product_id'],)).fetchone())
            quantity=int(line['quantity'])
            if not product or not 1<=quantity<=100000: fail('Choose a valid product and positive quantity for every line.')
            c.execute('INSERT INTO subscription_lines(subscription_id,product_id,name,quantity,unit_price) VALUES(?,?,?,?,?)',(sid,product['id'],product['name'],quantity,product['price']))
        validity=30
        if b.get('template_id'):
            template=c.execute('SELECT * FROM templates WHERE id=?',(b['template_id'],)).fetchone()
            if not template: fail('Template not found.')
            validity=template['valid_days']
        c.execute('UPDATE subscriptions SET quote_expires=? WHERE id=?',((today()+timedelta(days=validity)).isoformat(),sid))
        event(c,sid,'Subscription created',f"{p['name']} · {qty} seats")
    return {'id':sid}

@app.post('/api/subscriptions/{sid}/transition')
async def transition(sid:int,request:Request):
    u=user(request); b=await request.json(); action=b['action']
    with database() as c:
        s=owned(c,sid,u); p=row(c.execute('SELECT * FROM plans WHERE id=?',(s['plan_id'],)).fetchone())
        transitions={'quote':('Draft','Quotation'),'confirm':('Quotation','Confirmed'),'activate':('Confirmed','Active'),'pause':('Active','Paused'),'resume':('Paused','Active'),'close':(('Active','Paused'),'Closed'),'renew':('Closed','Active')}
        if action not in transitions: fail('Unknown action.')
        if u['role']=='customer' and action not in ('pause','resume','close','renew'): fail('Your team must approve this subscription.',403)
        source,target=transitions[action]
        if s['status'] not in (source if isinstance(source,tuple) else (source,)): fail('That action is unavailable for the current status.',409)
        if action=='confirm' and s.get('quote_expires') and s['quote_expires']<today().isoformat(): fail('This quotation has expired. Create a new quotation.')
        for a,flag in [('pause','can_pause'),('close','can_close'),('renew','can_renew')]:
            if action==a and not p[flag]: fail('This plan does not permit this action.')
        if action=='renew':
            c.execute('UPDATE subscriptions SET start_date=?,end_date=?,next_bill=? WHERE id=?',(today().isoformat(),(today()+timedelta(days=365)).isoformat(),today().isoformat(),sid))
        if action=='resume': c.execute('UPDATE subscriptions SET next_bill=? WHERE id=?',(max(today().isoformat(),s['next_bill']),sid))
        c.execute('UPDATE subscriptions SET status=? WHERE id=?',(target,sid)); event(c,sid,'Subscription '+target.lower(),f"Updated by {u['name']}")
    return {'ok':True}

def calculate(c,s,period,usage=None):
    lines=[dict(r) for r in c.execute('SELECT * FROM subscription_lines WHERE subscription_id=?',(s['id'],))]
    if not lines: lines=[{'product_id':s['product_id'],'name':c.execute('SELECT name FROM products WHERE id=?',(s['product_id'],)).fetchone()[0],'quantity':s['quantity'],'unit_price':s['unit_price']}]
    subtotal=sum(l['quantity']*l['unit_price'] for l in lines); discount=0; reason=None; did=None
    if s['discount_id']:
        d=row(c.execute('SELECT * FROM discounts WHERE id=?',(s['discount_id'],)).fetchone())
        if d:
            used=usage.get(d['id'],d['uses']) if usage is not None else d['uses']
            eligible=[l for l in lines if d['product_id'] is None or l['product_id']==d['product_id']]
            base=sum(l['quantity']*l['unit_price'] for l in eligible)
            if d['start_date']<=period<=d['end_date'] and used<d['max_uses'] and sum(l['quantity'] for l in eligible)>=d['min_quantity'] and base>=d['min_amount'] and eligible:
                discount=min(base,(base*d['value']+5000)//10000 if d['kind']=='percentage' else d['value']); did=d['id']
                if usage is not None: usage[did]=used+1
            else: reason='Discount is expired, exhausted, or ineligible.'
    tax=((subtotal-discount)*s['tax_rate']+5000)//10000
    return {'subtotal':subtotal,'discount':discount,'tax':tax,'total':subtotal-discount+tax,'discount_id':did,'warning':reason,'lines':lines}

def rehearsal(c,until):
    items=[]; usage={}
    for r in c.execute("SELECT s.*,u.name customer,p.name plan,p.interval,p.auto_close FROM subscriptions s JOIN users u ON u.id=s.customer_id JOIN plans p ON p.id=s.plan_id WHERE s.status='Active' ORDER BY s.id"):
        s=dict(r); period=s['next_bill']; count=0
        while period<=until and count<400:
            if period>s['end_date']: break
            if not c.execute('SELECT id FROM invoices WHERE subscription_id=? AND period=?',(s['id'],period)).fetchone():
                calc=calculate(c,s,period,usage)
                overdue=c.execute("SELECT COUNT(*) FROM invoices WHERE subscription_id=? AND status='Confirmed' AND due_date<?",(s['id'],today().isoformat())).fetchone()[0]
                items.append({'subscription_id':s['id'],'customer':s['customer'],'plan':s['plan'],'period':period,**calc,'risk':'Outstanding overdue invoice' if overdue else calc['warning']})
            period=advance(period,s['interval'],s['start_date']); count+=1
    return items

@app.get('/api/rehearsal')
def preview(request:Request,days:int=7):
    user(request,['admin','staff'])
    if not 0<=days<=90: fail('Choose a horizon between 0 and 90 days.')
    with database() as c: items=rehearsal(c,(today()+timedelta(days=days)).isoformat())
    return {'items':items,'total':sum(i['total'] for i in items),'at_risk':sum(i['total'] for i in items if i['risk']),'days':days}

@app.post('/api/billing/run')
def billing_run(request:Request):
    user(request,['admin','staff'])
    return process_billing()

@app.post('/api/internal/billing')
def scheduled_billing(request:Request):
    token=os.getenv('CADENCE_BILLING_TOKEN','')
    supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
    if not token or not hmac.compare_digest(token,supplied): fail('Unauthorized scheduler.',401)
    return process_billing()

def process_billing():
    count=0
    with database() as c:
        c.execute('BEGIN IMMEDIATE')
        for item in rehearsal(c,today().isoformat()):
            s=dict(c.execute('SELECT * FROM subscriptions WHERE id=?',(item['subscription_id'],)).fetchone())
            iid=c.execute('INSERT INTO invoices(subscription_id,period,subtotal,discount,tax,total,status,due_date,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(s['id'],item['period'],item['subtotal'],item['discount'],item['tax'],item['total'],'Draft',(date.fromisoformat(item['period'])+timedelta(days=s['payment_terms'])).isoformat(),now())).lastrowid
            for l in item['lines']: c.execute('INSERT INTO invoice_lines(invoice_id,name,quantity,unit_price) VALUES(?,?,?,?)',(iid,l['name'],l['quantity'],l['unit_price']))
            if item['discount_id']: c.execute('UPDATE discounts SET uses=uses+1 WHERE id=?',(item['discount_id'],))
            interval=c.execute('SELECT interval FROM plans WHERE id=?',(s['plan_id'],)).fetchone()[0]
            c.execute('UPDATE subscriptions SET next_bill=? WHERE id=?',(advance(item['period'],interval,s['start_date']),s['id']))
            event(c,s['id'],'Invoice drafted',f"Billing period {item['period']} · ready for review"); count+=1
        c.execute("UPDATE subscriptions SET status='Closed' WHERE status='Active' AND end_date<? AND plan_id IN (SELECT id FROM plans WHERE auto_close=1)",(today().isoformat(),))
    return {'created':count}

@app.post('/api/invoices/{iid}/{action}')
async def invoice_action(iid:int,action:str,request:Request):
    u=user(request,['admin','staff'])
    with database() as c:
        c.execute('BEGIN IMMEDIATE')
        inv=row(c.execute('SELECT * FROM invoices WHERE id=?',(iid,)).fetchone())
        if not inv: fail('Invoice not found.',404)
        if action=='confirm':
            if inv['status']!='Draft': fail('Only draft invoices can be confirmed.')
            c.execute("UPDATE invoices SET status='Confirmed' WHERE id=?",(iid,))
        elif action=='cancel':
            if inv['status'] not in ('Draft','Confirmed') or c.execute('SELECT COUNT(*) FROM payments WHERE invoice_id=?',(iid,)).fetchone()[0]: fail('An invoice with payments cannot be cancelled.')
            c.execute("UPDATE invoices SET status='Cancelled' WHERE id=?",(iid,))
        elif action=='pay':
            b=await request.json(); amount=cents(b['amount'])
            if not b.get('idempotency_key'): fail('Payment reference is required.')
            if c.execute('SELECT id FROM payments WHERE idempotency_key=?',(b['idempotency_key'],)).fetchone(): return {'ok':True,'duplicate':True}
            paid=c.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?',(iid,)).fetchone()[0]
            if inv['status']!='Confirmed' or not 0<amount<=inv['total']-paid: fail('Enter a positive payment within the outstanding balance of a confirmed invoice.')
            if b.get('method') not in ('Bank transfer','Card','Cash','UPI'): fail('Choose a payment method.')
            paydate=b.get('date',today().isoformat())
            if paydate>today().isoformat(): fail('Payment date cannot be in the future.')
            c.execute('INSERT INTO payments(invoice_id,amount,method,date,note,idempotency_key) VALUES(?,?,?,?,?,?)',(iid,amount,b['method'],paydate,b.get('note',''),b['idempotency_key']))
            if paid+amount==inv['total']: c.execute("UPDATE invoices SET status='Paid' WHERE id=?",(iid,))
        elif action=='send':
            if inv['status'] not in ('Confirmed','Paid'): fail('Confirm the invoice before sending.')
            email=c.execute('SELECT u.email FROM users u JOIN subscriptions s ON s.customer_id=u.id WHERE s.id=?',(inv['subscription_id'],)).fetchone()[0]
            c.execute('INSERT INTO outbox(recipient,subject,body,created_at) VALUES(?,?,?,?)',(email,f'Cadence invoice INV-{iid:04d}',f"Your invoice total is INR {inv['total']/100:.2f}. View it in your customer portal.",now()))
        else: fail('Unknown action.',404)
        event(c,inv['subscription_id'],'Invoice '+{'pay':'payment recorded','send':'queued for email','confirm':'confirmed','cancel':'cancelled'}[action],f'INV-{iid:04d} · {u["name"]}')
    return {'ok':True}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/{path:path}')
def index(path:str): return FileResponse(ROOT/'static'/'index.html')
