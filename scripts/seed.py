from app.main import SessionLocal, Partner, Location, Machine, ServiceType, open_machine_location
from sqlalchemy import select
from decimal import Decimal
from datetime import date

with SessionLocal() as s:
    if not s.scalar(select(Location).where(Location.name=="Battlemage Brewing")):
        s.add(Location(name="Battlemage Brewing", venue_share_pct=Decimal("0.20")))
    for name,pct in [("Felix",.425),("Matt",.425),("Sean",.15)]:
        if not s.scalar(select(Partner).where(Partner.name==name)):
            s.add(Partner(name=name,ownership_pct=Decimal(str(pct))))
    services=[
      ("Clean Glass",None,None,7,14),
      ("Clean Playfield",700,1000,None,None),
      ("New Balls",800,1000,None,None),
      ("Rubbers",5000,5000,None,None),
    ]
    for name,y,r,dy,dr in services:
        if not s.scalar(select(ServiceType).where(ServiceType.name==name)):
            s.add(ServiceType(name=name,threshold_plays_yellow=y,threshold_plays_red=r,
                              threshold_days_yellow=dy,threshold_days_red=dr))
    s.commit()
    loc=s.scalar(select(Location).where(Location.name=="Battlemage Brewing"))
    existing={
      "Dungeons & Dragons":("D&D","Pro","397061",date(2025,4,20),Decimal("7950"),3893),
      "Star Wars Pro":("Stern","Pro","279284",date(2025,9,19),None,568),
    }
    for name,(man,model,stern,pd,cost,base) in existing.items():
        if not s.scalar(select(Machine).where(Machine.stern_machine_id==stern)):
            m=Machine(name=name,manufacturer="Stern",model=model,stern_machine_id=stern,
                      purchase_date=pd,purchase_cost=cost,odometer_baseline=base)
            s.add(m); s.flush()
            open_machine_location(s, m, loc.id, pd, notes="Seeded from legacy install date")
    s.commit()
    print("Seed complete. Add Pokémon from the Machines screen with its Stern machine ID.")
