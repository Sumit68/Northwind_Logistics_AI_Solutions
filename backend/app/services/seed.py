import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Employee


def seed_employees(db: Session) -> int:
    count = 0
    base = settings.submissions_path
    if not base.exists():
        return 0
    for folder in sorted(base.iterdir()):
        info_path = folder / "employee_info.json"
        if not info_path.exists():
            continue
        data = json.loads(info_path.read_text())
        existing = (
            db.query(Employee).filter(Employee.employee_id == data["employee_id"]).first()
        )
        if existing:
            continue
        emp = Employee(
            employee_id=data["employee_id"],
            name=data["name"],
            grade=data["grade"],
            title=data["title"],
            department=data["department"],
            manager_id=data["manager_id"],
            home_base=data["home_base"],
            trip_purpose=data.get("trip_purpose"),
            trip_dates=data.get("trip_dates"),
        )
        db.add(emp)
        count += 1
    db.commit()
    return count
