from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json, os, secrets, uuid

from fastapi import FastAPI, Request, Form, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import (
    create_engine, String, Integer, DateTime, Date, Numeric, Boolean, ForeignKey,
    Text, select, func, desc, or_
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from pydantic import BaseModel
from passlib.hash import bcrypt

DATABASE_URL = os.environ["DATABASE_URL"]
RAW_DATA_DIR = Path(os.getenv("RAW_DATA_DIR", "/data/raw"))
SCRAPER_API_KEY = os.environ["SCRAPER_API_KEY"]
APP_SECRET = os.environ["APP_SECRET"]
# Password gate for the web UI. Generate a hash with scripts/hash_password.py
# and put it in .env as APP_PASSWORD_HASH. Cloudflare Access (or similar) in
# front of the tunnel is still the primary control -- this is defense in depth
# for the case that gets misconfigured or skipped.
APP_PASSWORD_HASH = os.environ["APP_PASSWORD_HASH"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class Partner(Base):
    __tablename__="partners"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(100), unique=True)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    ownership_pct: Mapped[Decimal]=mapped_column(Numeric(8,5), default=0)
    ledger = relationship("LedgerEntry", back_populates="partner")

class Location(Base):
    __tablename__="locations"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(150), unique=True)
    address: Mapped[str|None]=mapped_column(String(300))
    venue_share_pct: Mapped[Decimal]=mapped_column(Numeric(8,5), default=Decimal("0.20"))
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    # Bearer token for the location's read-only collection portal
    # (see /portal/{token} below). Long and random -- knowing it IS the auth.
    # Regenerate it (new random value) any time a link needs to be killed.
    access_token: Mapped[str]=mapped_column(String(64), unique=True, index=True,
                                             default=lambda: secrets.token_urlsafe(24))
    machines=relationship("Machine", back_populates="location")
    collections=relationship("Collection", back_populates="location")

class Machine(Base):
    __tablename__="machines"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(150))
    manufacturer: Mapped[str|None]=mapped_column(String(100))
    model: Mapped[str|None]=mapped_column(String(100))
    serial_number: Mapped[str|None]=mapped_column(String(100))
    stern_machine_id: Mapped[str|None]=mapped_column(String(100), index=True)
    purchase_date: Mapped[date|None]=mapped_column(Date)
    purchase_cost: Mapped[Decimal|None]=mapped_column(Numeric(12,2))
    odometer_baseline: Mapped[int]=mapped_column(Integer, default=0)
    status: Mapped[str]=mapped_column(String(30), default="active")
    # location_id is a CACHE of "where is this machine right now", kept in sync
    # by move_machine(). It answers "what's at Battlemage today" cheaply for the
    # collection/maintenance screens. It is NOT the source of truth for history --
    # machine_location_history is. Never write to this column directly outside
    # of add_machine()/move_machine().
    location_id: Mapped[int|None]=mapped_column(ForeignKey("locations.id"))
    notes: Mapped[str|None]=mapped_column(Text)
    location=relationship("Location", back_populates="machines")
    snapshots=relationship("PlaySnapshot", back_populates="machine")
    services=relationship("ServiceLog", back_populates="machine")
    collections=relationship("CollectionLine", back_populates="machine")
    purchases=relationship("Purchase", back_populates="machine")
    location_history=relationship("MachineLocationHistory", back_populates="machine",
                                   order_by="MachineLocationHistory.start_date")

class MachineLocationHistory(Base):
    """Where a machine actually was, over time. Source of truth for location --
    Machine.location_id is just a cached read of the open-ended row here.
    Exactly one row per machine should have end_date IS NULL at a time."""
    __tablename__="machine_location_history"
    id: Mapped[int]=mapped_column(primary_key=True)
    machine_id: Mapped[int]=mapped_column(ForeignKey("machines.id"), index=True)
    location_id: Mapped[int]=mapped_column(ForeignKey("locations.id"))
    start_date: Mapped[date]=mapped_column(Date, index=True)
    end_date: Mapped[date|None]=mapped_column(Date, index=True)
    notes: Mapped[str|None]=mapped_column(Text)
    machine=relationship("Machine", back_populates="location_history")
    location=relationship("Location")

class PlaySnapshot(Base):
    __tablename__="play_snapshots"
    id: Mapped[int]=mapped_column(primary_key=True)
    machine_id: Mapped[int]=mapped_column(ForeignKey("machines.id"), index=True)
    # Location the machine was actually AT when this snapshot was captured, looked
    # up from machine_location_history at write time. This is what makes
    # "plays at Battlemage in June" answerable after a machine has moved --
    # Machine.location_id alone can't do that once it's been overwritten by a move.
    location_id: Mapped[int|None]=mapped_column(ForeignKey("locations.id"), index=True)
    captured_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    daily_plays: Mapped[int]=mapped_column(Integer, default=0)
    seven_day_plays: Mapped[int|None]=mapped_column(Integer)
    twenty_eight_day_plays: Mapped[int|None]=mapped_column(Integer)
    cumulative_plays: Mapped[int]=mapped_column(Integer)
    source: Mapped[str]=mapped_column(String(50), default="stern")
    raw_file: Mapped[str|None]=mapped_column(String(500))
    machine=relationship("Machine", back_populates="snapshots")

class Collection(Base):
    __tablename__="collections"
    id: Mapped[int]=mapped_column(primary_key=True)
    collected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    location_id: Mapped[int]=mapped_column(ForeignKey("locations.id"))
    cdm_rep: Mapped[str|None]=mapped_column(String(100))
    venue_rep: Mapped[str|None]=mapped_column(String(100))
    actual_cash_paid: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    gross_total: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    venue_share: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    cdm_net: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    notes: Mapped[str|None]=mapped_column(Text)
    location=relationship("Location", back_populates="collections")
    lines=relationship("CollectionLine", back_populates="collection", cascade="all, delete-orphan")

class CollectionLine(Base):
    __tablename__="collection_lines"
    id: Mapped[int]=mapped_column(primary_key=True)
    collection_id: Mapped[int]=mapped_column(ForeignKey("collections.id"))
    machine_id: Mapped[int]=mapped_column(ForeignKey("machines.id"))
    quarters: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    bills: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    gross: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    play_count: Mapped[int|None]=mapped_column(Integer)
    collection=relationship("Collection", back_populates="lines")
    machine=relationship("Machine", back_populates="collections")

class ServiceType(Base):
    __tablename__="service_types"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(100), unique=True)
    threshold_plays_yellow: Mapped[int|None]=mapped_column(Integer)
    threshold_plays_red: Mapped[int|None]=mapped_column(Integer)
    threshold_days_yellow: Mapped[int|None]=mapped_column(Integer)
    threshold_days_red: Mapped[int|None]=mapped_column(Integer)

class ServiceLog(Base):
    __tablename__="service_logs"
    id: Mapped[int]=mapped_column(primary_key=True)
    serviced_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), index=True)
    machine_id: Mapped[int]=mapped_column(ForeignKey("machines.id"))
    location_id: Mapped[int|None]=mapped_column(ForeignKey("locations.id"))
    service_type_id: Mapped[int]=mapped_column(ForeignKey("service_types.id"))
    rep: Mapped[str|None]=mapped_column(String(100))
    play_count: Mapped[int|None]=mapped_column(Integer)
    cost: Mapped[Decimal]=mapped_column(Numeric(12,2), default=0)
    notes: Mapped[str|None]=mapped_column(Text)
    machine=relationship("Machine", back_populates="services")
    service_type=relationship("ServiceType")

class Purchase(Base):
    __tablename__="purchases"
    id: Mapped[int]=mapped_column(primary_key=True)
    purchase_date: Mapped[date]=mapped_column(Date)
    description: Mapped[str]=mapped_column(String(300))
    vendor: Mapped[str|None]=mapped_column(String(150))
    amount: Mapped[Decimal]=mapped_column(Numeric(12,2))
    paid_by: Mapped[str|None]=mapped_column(String(100))
    trued_up: Mapped[bool]=mapped_column(Boolean, default=False)
    machine_id: Mapped[int|None]=mapped_column(ForeignKey("machines.id"))
    location_id: Mapped[int|None]=mapped_column(ForeignKey("locations.id"))
    notes: Mapped[str|None]=mapped_column(Text)
    machine=relationship("Machine", back_populates="purchases")

class LedgerEntry(Base):
    __tablename__="ledger_entries"
    id: Mapped[int]=mapped_column(primary_key=True)
    entry_date: Mapped[date]=mapped_column(Date)
    partner_id: Mapped[int|None]=mapped_column(ForeignKey("partners.id"))
    kind: Mapped[str]=mapped_column(String(50))
    amount: Mapped[Decimal]=mapped_column(Numeric(12,2))
    description: Mapped[str|None]=mapped_column(String(300))
    reference_id: Mapped[str|None]=mapped_column(String(100))
    partner=relationship("Partner", back_populates="ledger")

Base.metadata.create_all(engine)

# create_all() adds new tables but not new columns on existing tables. If this
# is deployed on top of a database from before access_token existed, add the
# column once by hand:
#   ALTER TABLE locations ADD COLUMN access_token VARCHAR(64) UNIQUE;
# then this backfills any NULL tokens on every startup (a no-op once they're set).
with SessionLocal() as _s:
    for _loc in _s.scalars(select(Location).where(Location.access_token.is_(None))).all():
        _loc.access_token=secrets.token_urlsafe(24)
    _s.commit()

app=FastAPI(title="CDM Ops")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates=Jinja2Templates(directory="app/templates")

# Routes reachable without a logged-in session. The scraper ingest endpoint and
# /api/machines have their own API-key check; health is for container
# healthchecks; login is login; /portal/ is the location-facing receipt view,
# gated by its own bearer token in the URL instead of a session.
PUBLIC_PATHS={"/login", "/api/health", "/api/plays/ingest", "/api/machines"}

@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static/") \
            or request.url.path.startswith("/portal/"):
        return await call_next(request)
    if not request.session.get("authed"):
        if request.method=="GET":
            return RedirectResponse(f"/login?next={request.url.path}", 303)
        raise HTTPException(401, "Not logged in")
    return await call_next(request)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str="/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})

@app.post("/login")
def login_submit(request: Request, password: str=Form(...), next: str=Form("/")):
    if bcrypt.verify(password, APP_PASSWORD_HASH):
        request.session["authed"]=True
        return RedirectResponse(next or "/", 303)
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": "Wrong password"}, status_code=401)

@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)

def db():
    return SessionLocal()

def money(v): return f"${Decimal(v or 0):,.2f}"

def current_plays(s, machine):
    row=s.scalar(select(PlaySnapshot).where(PlaySnapshot.machine_id==machine.id).order_by(desc(PlaySnapshot.captured_at)).limit(1))
    return row.cumulative_plays if row else machine.odometer_baseline

def location_id_at(s, machine_id:int, at_date:date):
    """The machine's location on a given date, per machine_location_history.
    This is what play/collection/service reporting should join through once a
    machine has moved -- NOT Machine.location_id, which only reflects 'now'."""
    row=s.scalar(select(MachineLocationHistory).where(
        MachineLocationHistory.machine_id==machine_id,
        MachineLocationHistory.start_date<=at_date,
        or_(MachineLocationHistory.end_date.is_(None), MachineLocationHistory.end_date>=at_date),
    ))
    return row.location_id if row else None

def open_machine_location(s, machine:"Machine", location_id:int, start_date:date, notes:str|None=None):
    """Set a machine's location, closing out any currently-open history row first.
    This is the ONLY place Machine.location_id should be written."""
    open_row=s.scalar(select(MachineLocationHistory).where(
        MachineLocationHistory.machine_id==machine.id, MachineLocationHistory.end_date.is_(None)))
    if open_row and open_row.location_id==location_id:
        return  # no-op, already there
    if open_row:
        open_row.end_date=start_date
    s.add(MachineLocationHistory(machine_id=machine.id, location_id=location_id,
                                  start_date=start_date, end_date=None, notes=notes))
    machine.location_id=location_id

def plays_since_previous_collection(s, machine_id:int, collected_at:datetime, play_count:int|None):
    """Games generated since the machine's prior collection, for receipt display.
    Falls back to None (shown as '—') for a machine's very first collection,
    since there's nothing to diff against."""
    if play_count is None: return None
    prev=s.scalar(select(CollectionLine).join(Collection)
        .where(CollectionLine.machine_id==machine_id, Collection.collected_at<collected_at,
               CollectionLine.play_count.is_not(None))
        .order_by(desc(Collection.collected_at)).limit(1))
    return (play_count-prev.play_count) if prev else None

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db() as s:
        locations=s.scalars(select(Location).where(Location.active==True).order_by(Location.name)).all()
        machines=s.scalars(select(Machine).order_by(Machine.name)).all()
        gross=s.scalar(select(func.coalesce(func.sum(Collection.gross_total),0))) or 0
        venue=s.scalar(select(func.coalesce(func.sum(Collection.venue_share),0))) or 0
        net=s.scalar(select(func.coalesce(func.sum(Collection.cdm_net),0))) or 0
        last30=datetime.now(timezone.utc)-timedelta(days=30)
        plays30=s.scalar(select(func.coalesce(func.sum(PlaySnapshot.daily_plays),0)).where(PlaySnapshot.captured_at>=last30)) or 0
        cards=[{"machine":m,"plays":current_plays(s,m)} for m in machines]
    return templates.TemplateResponse("dashboard.html",{"request":request,"locations":locations,"cards":cards,
        "gross":money(gross),"venue":money(venue),"net":money(net),"plays30":plays30})

@app.get("/collection", response_class=HTMLResponse)
def collection_page(request: Request, location_id:int|None=None):
    with db() as s:
        locations=s.scalars(select(Location).where(Location.active==True).order_by(Location.name)).all()
        if not location_id and locations: location_id=locations[0].id
        machines=s.scalars(select(Machine).where(Machine.location_id==location_id,Machine.status=="active").order_by(Machine.name)).all() if location_id else []
        rows=[{"machine":m,"plays":current_plays(s,m)} for m in machines]
        partners=s.scalars(select(Partner).where(Partner.active==True).order_by(Partner.name)).all()
    return templates.TemplateResponse("collection.html",{"request":request,"locations":locations,"selected":location_id,"rows":rows,"partners":partners})

@app.post("/collection")
async def save_collection(request: Request, location_id:int=Form(...), cdm_rep:str=Form(...), venue_rep:str=Form(""),
                    actual_cash_paid:Decimal=Form(0), notes:str=Form("")):
    with db() as s:
        loc=s.get(Location,location_id)
        if not loc: raise HTTPException(404,"Location not found")
        machines=s.scalars(select(Machine).where(Machine.location_id==location_id,Machine.status=="active").order_by(Machine.id)).all()
        form=await request.form()
        lines=[]; gross=Decimal("0")
        for m in machines:
            q=Decimal(str(form.get(f"q_{m.id}",0) or 0))
            b=Decimal(str(form.get(f"b_{m.id}",0) or 0))
            g=q*Decimal("0.25")+b
            if g or q or b:
                lines.append((m,q,b,g,current_plays(s,m))); gross+=g
        venue_share=gross*Decimal(loc.venue_share_pct)
        c=Collection(collected_at=datetime.now(timezone.utc),location_id=location_id,cdm_rep=cdm_rep,venue_rep=venue_rep,
                     actual_cash_paid=actual_cash_paid,gross_total=gross,venue_share=venue_share,cdm_net=gross-venue_share,notes=notes)
        s.add(c); s.flush()
        for m,q,b,g,p in lines: s.add(CollectionLine(collection_id=c.id,machine_id=m.id,quarters=q,bills=b,gross=g,play_count=p))
        s.commit()
    return RedirectResponse("/collection?location_id="+str(location_id),303)

@app.get("/maintenance", response_class=HTMLResponse)
def maintenance_page(request: Request, location_id:int|None=None):
    with db() as s:
        locations=s.scalars(select(Location).where(Location.active==True).order_by(Location.name)).all()
        if not location_id and locations: location_id=locations[0].id
        machines=s.scalars(select(Machine).where(Machine.location_id==location_id,Machine.status=="active").order_by(Machine.name)).all() if location_id else []
        types=s.scalars(select(ServiceType).order_by(ServiceType.name)).all()
        status=[]
        for m in machines:
            for t in types:
                last=s.scalar(select(ServiceLog).where(ServiceLog.machine_id==m.id,ServiceLog.service_type_id==t.id).order_by(desc(ServiceLog.serviced_at)).limit(1))
                cp=current_plays(s,m)
                p_since=(cp-last.play_count) if last and last.play_count is not None else None
                d_since=((datetime.now(timezone.utc)-last.serviced_at).days) if last else None
                level="red" if not last else "green"
                if last:
                    if t.threshold_plays_red and p_since>=t.threshold_plays_red: level="red"
                    elif t.threshold_plays_yellow and p_since>=t.threshold_plays_yellow: level="yellow"
                    elif t.threshold_days_red and d_since>=t.threshold_days_red: level="red"
                    elif t.threshold_days_yellow and d_since>=t.threshold_days_yellow: level="yellow"
                status.append((m,t,last,p_since,d_since,level))
    return templates.TemplateResponse("maintenance.html",{"request":request,"locations":locations,"selected":location_id,"status":status})

@app.post("/maintenance")
def save_service(machine_id:int=Form(...),service_type_id:int=Form(...),rep:str=Form(...),cost:Decimal=Form(0),notes:str=Form("")):
    with db() as s:
        m=s.get(Machine,machine_id); t=s.get(ServiceType,service_type_id)
        if not m or not t: raise HTTPException(404,"Machine or service type not found")
        loc=m.location_id
        s.add(ServiceLog(serviced_at=datetime.now(timezone.utc),machine_id=machine_id,location_id=loc,service_type_id=service_type_id,
                         rep=rep,play_count=current_plays(s,m),cost=cost,notes=notes))
        s.commit()
    return RedirectResponse("/maintenance?location_id="+str(loc),303)

@app.get("/machines", response_class=HTMLResponse)
def machines_page(request:Request):
    with db() as s: machines=s.scalars(select(Machine).order_by(Machine.name)).all(); locations=s.scalars(select(Location).order_by(Location.name)).all()
    return templates.TemplateResponse("machines.html",{"request":request,"machines":machines,"locations":locations})

@app.post("/machines")
def add_machine(name:str=Form(...),location_id:int=Form(...),manufacturer:str=Form("Stern"),model:str=Form(""),stern_machine_id:str=Form(""),
                purchase_date:str=Form(""),purchase_cost:Decimal|None=Form(None),odometer_baseline:int=Form(0),notes:str=Form("")):
    with db() as s:
        pd=date.fromisoformat(purchase_date) if purchase_date else None
        m=Machine(name=name,manufacturer=manufacturer or None,model=model or None,
                  stern_machine_id=stern_machine_id or None,purchase_date=pd,purchase_cost=purchase_cost,
                  odometer_baseline=odometer_baseline,notes=notes or None)
        s.add(m); s.flush()
        open_machine_location(s, m, location_id, pd or date.today(), notes="Initial install")
        s.commit()
    return RedirectResponse("/machines",303)

@app.post("/machines/{machine_id}/move")
def move_machine(machine_id:int, new_location_id:int=Form(...), effective_date:str=Form(...), notes:str=Form("")):
    with db() as s:
        m=s.get(Machine, machine_id)
        if not m: raise HTTPException(404,"Machine not found")
        open_machine_location(s, m, new_location_id, date.fromisoformat(effective_date), notes=notes or None)
        s.commit()
    return RedirectResponse("/machines",303)

@app.get("/locations", response_class=HTMLResponse)
def locations_page(request:Request):
    with db() as s: locations=s.scalars(select(Location).order_by(Location.name)).all()
    return templates.TemplateResponse("locations.html",{"request":request,"locations":locations})

@app.post("/locations")
def add_location(name:str=Form(...),venue_share_pct:Decimal=Form(0.20),address:str=Form("")):
    with db() as s: s.add(Location(name=name,address=address or None,venue_share_pct=venue_share_pct)); s.commit()
    return RedirectResponse("/locations",303)

@app.post("/locations/{location_id}/regenerate-portal-link")
def regenerate_portal_link(location_id:int):
    with db() as s:
        loc=s.get(Location, location_id)
        if not loc: raise HTTPException(404,"Location not found")
        loc.access_token=secrets.token_urlsafe(24)
        s.commit()
    return RedirectResponse("/locations",303)

# No-cache / no-index headers for the public receipt portal: it's a bearer link
# that's meant to live indefinitely, so it should never get cached by a shared
# proxy/browser or picked up by a crawler that follows a forwarded email.
PORTAL_HEADERS={"Cache-Control":"no-store","X-Robots-Tag":"noindex, nofollow"}

@app.get("/portal/{token}", response_class=HTMLResponse)
def location_portal(request:Request, token:str):
    with db() as s:
        loc=s.scalar(select(Location).where(Location.access_token==token))
        if not loc:
            # Generic 404, not a "bad token" message -- don't confirm/deny token
            # validity to a caller who doesn't already have the right one.
            raise HTTPException(404)
        collections=s.scalars(select(Collection).where(Collection.location_id==loc.id)
            .order_by(desc(Collection.collected_at)).limit(50)).all()
        receipts=[]
        for c in collections:
            lines=[{"machine":l.machine.name,
                    "plays":plays_since_previous_collection(s,l.machine_id,c.collected_at,l.play_count),
                    "revenue":l.gross} for l in c.lines]
            receipts.append({"collection":c,"lines":lines})
    return templates.TemplateResponse("portal.html",
        {"request":request,"location":loc,"receipts":receipts}, headers=PORTAL_HEADERS)

@app.get("/purchases", response_class=HTMLResponse)
def purchases_page(request:Request):
    with db() as s:
        purchases=s.scalars(select(Purchase).order_by(desc(Purchase.purchase_date),desc(Purchase.id))).all()
        machines=s.scalars(select(Machine).order_by(Machine.name)).all()
    return templates.TemplateResponse("purchases.html",{"request":request,"purchases":purchases,"machines":machines})

@app.post("/purchases")
def add_purchase(purchase_date:str=Form(...),description:str=Form(...),amount:Decimal=Form(...),vendor:str=Form(""),paid_by:str=Form(""),
                 trued_up:bool=Form(False),machine_id:int|None=Form(None),notes:str=Form("")):
    with db() as s:
        s.add(Purchase(purchase_date=date.fromisoformat(purchase_date),description=description,amount=amount,vendor=vendor or None,
                       paid_by=paid_by or None,trued_up=trued_up,machine_id=machine_id,notes=notes or None)); s.commit()
    return RedirectResponse("/purchases",303)

@app.get("/partners", response_class=HTMLResponse)
def partners_page(request:Request):
    with db() as s: partners=s.scalars(select(Partner).order_by(Partner.name)).all()
    return templates.TemplateResponse("partners.html",{"request":request,"partners":partners})

@app.post("/partners")
def add_partner(name:str=Form(...),ownership_pct:Decimal=Form(0)):
    with db() as s:
        if not s.scalar(select(Partner).where(Partner.name==name)):
            s.add(Partner(name=name,ownership_pct=ownership_pct)); s.commit()
    return RedirectResponse("/partners",303)

@app.post("/partners/{partner_id}/toggle-active")
def toggle_partner_active(partner_id:int):
    with db() as s:
        p=s.get(Partner, partner_id)
        if not p: raise HTTPException(404,"Partner not found")
        p.active=not p.active; s.commit()
    return RedirectResponse("/partners",303)

class PlayRow(BaseModel):
    stern_machine_id:str
    title:str
    captured_at:datetime
    today:int=0
    seven_days:int|None=None
    twenty_eight_days:int|None=None

class PlayPayload(BaseModel):
    rows:list[PlayRow]

@app.post("/api/plays/ingest")
def ingest(payload:PlayPayload, x_api_key:str=Header(default="")):
    if x_api_key!=SCRAPER_API_KEY: raise HTTPException(401,"Bad API key")
    RAW_DATA_DIR.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    raw=RAW_DATA_DIR/f"stern_{stamp}.json"
    raw.write_text(json.dumps([r.model_dump(mode="json") for r in payload.rows],indent=2))
    skipped=[]
    with db() as s:
        for r in payload.rows:
            m=s.scalar(select(Machine).where(Machine.stern_machine_id==r.stern_machine_id))
            if not m:
                # Don't silently drop plays for an unrecognized Stern ID -- that's
                # exactly how a title-parsing miss would go unnoticed. Log it so a
                # scraper glitch or a new/renamed machine shows up somewhere.
                skipped.append(r.stern_machine_id)
                continue
            captured_date=r.captured_at.date()
            prev=s.scalar(select(PlaySnapshot).where(PlaySnapshot.machine_id==m.id).order_by(desc(PlaySnapshot.captured_at)).limit(1))
            # Guard against the scraper running twice in the same day and double
            # counting Stern's "today" figure into the cumulative odometer.
            if prev and prev.captured_at.astimezone(timezone.utc).date()==captured_date:
                continue
            cumulative=(prev.cumulative_plays if prev else m.odometer_baseline)+max(0,r.today)
            s.add(PlaySnapshot(machine_id=m.id,location_id=location_id_at(s, m.id, captured_date) or m.location_id,
                               captured_at=r.captured_at,daily_plays=r.today,
                               seven_day_plays=r.seven_days,twenty_eight_day_plays=r.twenty_eight_days,
                               cumulative_plays=cumulative,raw_file=str(raw)))
        s.commit()
    if skipped:
        print(f"WARNING: ingest saw unknown stern_machine_id(s): {skipped}")
    return {"ok":True,"rows":len(payload.rows),"skipped_ids":skipped,"raw_file":str(raw)}

@app.get("/api/health")
def health(): return {"ok":True}

@app.get("/api/machines")
def api_machines(x_api_key:str=Header(default="")):
    # Bypasses the session login (see PUBLIC_PATHS) so scripts can call it, so
    # it needs its own gate -- same API key as the scraper ingest endpoint.
    if x_api_key!=SCRAPER_API_KEY: raise HTTPException(401,"Bad API key")
    with db() as s:
        ms=s.scalars(select(Machine).order_by(Machine.name)).all()
        return [{"id":m.id,"name":m.name,"stern_machine_id":m.stern_machine_id,"location_id":m.location_id,"plays":current_plays(s,m)} for m in ms]
