"""
Import the supplied legacy workbooks into CDM Ops.

Place these files in ./imports:
  Daily Play Tracker.xlsx
  CDM Expense & Maintenance.xlsx
  BM Audit.xlsx

Run inside the app container:
  python scripts/import_legacy.py

This importer is intentionally additive. It does not delete existing records.
"""
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import openpyxl
from sqlalchemy import select
from app.main import SessionLocal, Machine, PlaySnapshot, ServiceLog, ServiceType, Purchase, Location

IMPORT=Path("/imports")

def get_machine(s, stern_id):
    return s.scalar(select(Machine).where(Machine.stern_machine_id==str(stern_id)))

with SessionLocal() as s:
    loc=s.scalar(select(Location).where(Location.name=="Battlemage Brewing"))
    # Play history
    p=IMPORT/"Daily Play Tracker.xlsx"
    if p.exists():
        wb=openpyxl.load_workbook(p,data_only=True)
        ws=wb["Sheet1"]
        for row in ws.iter_rows(min_row=2,values_only=True):
            ts,title,today,seven,twenty8,*_=row
            if not ts or not title: continue
            sid=None
            if "397061" in str(title): sid="397061"
            elif "279284" in str(title): sid="279284"
            if not sid: continue
            m=get_machine(s,sid)
            if not m: continue
            captured=ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            exists=s.scalar(select(PlaySnapshot).where(PlaySnapshot.machine_id==m.id,PlaySnapshot.captured_at==captured))
            if exists: continue
            prev=s.scalar(select(PlaySnapshot).where(PlaySnapshot.machine_id==m.id,PlaySnapshot.captured_at<captured).order_by(PlaySnapshot.captured_at.desc()).limit(1))
            cumulative=(prev.cumulative_plays if prev else m.odometer_baseline)+int(today or 0)
            # Both legacy machines lived at Battlemage for their whole history prior
            # to this migration, so the machine's current location is accurate here.
            # If you're importing data for a machine that moved historically, backfill
            # machine_location_history first so location_id_at() can resolve this per row.
            s.add(PlaySnapshot(machine_id=m.id,location_id=m.location_id,captured_at=captured,daily_plays=int(today or 0),
                seven_day_plays=int(seven or 0) if seven is not None else None,
                twenty_eight_day_plays=int(twenty8 or 0) if twenty8 is not None else None,cumulative_plays=cumulative,source="legacy"))
    # Purchases
    p=IMPORT/"CDM Expense & Maintenance.xlsx"
    if p.exists():
        wb=openpyxl.load_workbook(p,data_only=True)
        ws=wb["Purchases"]
        for r in ws.iter_rows(min_row=3,values_only=True):
            pd,desc,vendor,amount,*_=r[:10]
            if not pd or not desc or amount is None: continue
            paid_by=r[8] if len(r)>8 else None
            machine_id=None
            txt=str(desc).lower()
            if "dungeons" in txt or "dnd" in txt:
                m=s.scalar(select(Machine).where(Machine.stern_machine_id=="397061")); machine_id=m.id if m else None
            elif "star wars" in txt:
                m=s.scalar(select(Machine).where(Machine.stern_machine_id=="279284")); machine_id=m.id if m else None
            exists=s.scalar(select(Purchase).where(Purchase.purchase_date==pd,Purchase.description==str(desc),Purchase.amount==Decimal(str(amount))))
            if not exists:
                s.add(Purchase(purchase_date=pd,description=str(desc),vendor=str(vendor) if vendor else None,
                    amount=Decimal(str(amount)),paid_by=str(paid_by) if paid_by else None,machine_id=machine_id,location_id=loc.id if loc else None))
    # Service log
    p=IMPORT/"CDM Expense & Maintenance.xlsx"
    if p.exists():
        wb=openpyxl.load_workbook(p,data_only=True)
        ws=wb["Service Log"]
        type_cache={x.name:x.id for x in s.scalars(select(ServiceType)).all()}
        for r in ws.iter_rows(min_row=2,values_only=True):
            dt,machine,rep,task,plays=r[:5]
            if not dt or not machine or not task: continue
            sid="397061" if "dungeon" in str(machine).lower() else ("279284" if "star" in str(machine).lower() else None)
            m=get_machine(s,sid) if sid else None
            t=type_cache.get(str(task))
            if not m or not t: continue
            dt=dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            exists=s.scalar(select(ServiceLog).where(ServiceLog.machine_id==m.id,ServiceLog.serviced_at==dt,ServiceLog.service_type_id==t))
            if not exists:
                s.add(ServiceLog(serviced_at=dt,machine_id=m.id,location_id=m.location_id,service_type_id=t,
                    rep=str(rep) if rep else None,play_count=int(plays) if plays else None))
    s.commit()
print("Legacy import complete.")
