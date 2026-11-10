# Platform Attendance Log  (PySide6 + SQLAlchemy + SQLite + Pandas + OpenPyXL)

An offline desktop app that records, every day, who took service on the
platform: employee, structure, badge number, phone number, ID card number,
time in, time out, and date. No internet is ever used. Employees are
organised by structure.

Each employee has one current badge. Badges are never shared; when a badge
changes, previous attendance keeps the number recorded at that visit.

## 1. Install (once)
Use a recent Python (3.11 or newer is the safest choice).
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Check that everything works (do this first!)
```
python self_check.py --gui
```
It uses a temporary database, never your real records. Every line must
say OK. If a line says FAILED or CRASHED, copy the whole output and send
it to whoever is helping you. `--gui` also drives the real window
without showing it.

## 3. Run the app
```
python main.py
```

## A normal day
1. Employees tab (once): add the structures, then each employee with
   their own badge, phone number (`+` followed by 7-15 digits), and
   positive integer ID card number. Type a structure name in the employee
   form's dropdown to find it quickly.
2. Today tab: choose the Structure and the Employee. Their badge is
   shown. Give them that badge, keep their ID card, click "Check in".
   The time comes from the computer's clock.
3. When the employee leaves: use the Today tab's structure dropdown or
   search box to narrow the list if needed, click their row (green = still
   on the platform; red = they did not check out on their check-in date),
   click "Check out selected employee", give back the ID
   card and take back the badge. The visit stays in Today with its check-out
   time, including when the employee checked in on an earlier date.
4. History tab: search by date range, structure, name or badge.
   "Export to Excel" gives one file with three sheets: the records, a
   summary by structure and a summary by employee.

## Where the data is saved
`C:\Users\<your Windows user>\PlatformLog\platform_log.db`
(Help > "Where is my data?" shows the exact path.)
Every action is saved the instant it happens. There is no Save button.
That folder belongs to ONE Windows account, so always use the same
Windows account on the platform computer.

## Backups (do this regularly!)
File > Backup database... saves a full copy. Keep copies on a USB stick
or another computer, for example at the end of every week.

## Rules the app enforces
- A person cannot check in twice without checking out.
- Each employee must have a phone number and positive integer ID card
  number. Phone numbers use `+` followed by 7-15 digits. Existing employees
  need their details filled in from Employees > Update phone and ID card
  before they can check in.
- A badge belongs to one employee only. A deactivated employee keeps
  their badge reserved, so it can never go to someone else. If a badge
  changes, its old number remains reserved for the same employee and past
  attendance retains the badge used for that visit.
- Employees are never deleted, only deactivated, so old records keep
  their meaning.
- Names and structures are compared ignoring capitals and accents
  (Éric = eric = ERIC).
- The database itself also refuses a second open visit for the same
  person and a badge used twice (a safety net below the program).

## Make a program you can double-click
```
pyinstaller --noconfirm --onefile --windowed --name PlatformLog --icon platform_icon.ico --add-data "platform_icon.ico;." --hidden-import sqlalchemy.dialects.sqlite --distpath PlatForm main.py
```
The result is the single file `PlatForm\PlatformLog.exe`. Copy that file to
the platform computer and start it.
- It is big (hundreds of MB) because PySide6 and pandas are big.
- It starts more slowly than a folder build because it unpacks itself on
  every launch.
- Test the built program on the real computer: add one test employee,
  check in, check out, export to Excel, then delete the test data
  folder if you want a clean start.
The records live in the PlatformLog folder described above, so replacing
the program with a newer version never touches them.

## Known limits (on purpose, for version 1)
- One computer only. Do not share the database file between computers.
- Times come from the computer's clock. Keep the date and time correct.
- No login or user roles: anyone who opens the app can use everything.
- A record can't be edited. Attendance logs are usually kept untouchable;
  a "correction with a reason" feature would be the right next step.
- Existing records are updated on first launch after this version to store
  the employee's current badge and add the new contact fields. Earlier badge
  changes cannot be reconstructed for visits created before badge history
  was recorded; existing employees' new contact fields start empty.

## How the code is organised
- `models.py`    the tables, written as Python classes (SQLAlchemy ORM)
- `services.py`  everything the app can do, and all the rules
- `reports.py`   pandas tables and the Excel file (openpyxl)
- `common.py`    small shared pieces (names, times, the Record type)
- `main_window.py` the window and its three tabs (PySide6)
- `main.py`      starts the app
- `self_check.py` the safety check described above
