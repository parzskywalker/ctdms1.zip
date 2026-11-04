CTDMS PILOT KIT - read this first
=================================

WHAT THIS IS
  A complete copy of CTDMS that needs no installation, no internet and no administrator rights to run.
  It carries its own Python. Nothing on the PC is changed, except by OPEN_FIREWALL_ONCE.bat, which you run yourself.
  One PC (the "host PC") runs CTDMS. Every other PC just opens it in a web browser.

FIRST TIME ON THE HOST PC
  1. Copy this whole folder to  C:\CTDMS
  2. Double-click  CHECK_COPY.bat       -> it must say "COPY IS COMPLETE".
  3. Double-click  CHECK_THIS_PC.bat    -> shows Windows version, network and firewall (changes nothing).
  4. Right-click   OPEN_FIREWALL_ONCE.bat  ->  Run as administrator.  (Once. It lets the other PCs in.)
  5. Double-click  START_CTDMS.bat      -> the first time it asks you to create the administrator password.
  6. On the host PC the browser opens by itself. On every other PC open the address that START_CTDMS.bat prints,
     for example  http://192.168.1.20:8000

EVERY DAY
  Morning :  START_CTDMS.bat
  Evening :  STOP_CTDMS.bat, then BACKUP_NOW.bat (type the USB drive letter to copy the backup there)
  Keep the host PC switched on and plugged in during working hours. Do not close the minimised window "CTDMS server".

IF SOMETHING GOES WRONG
  * Other PCs cannot open the address ........ run CHECK_THIS_PC.bat; run OPEN_FIREWALL_ONCE.bat as administrator.
  * "Port 8000 already in use" ................ CTDMS is already running: open http://127.0.0.1:8000
                                                 or run STOP_CTDMS.bat and START_CTDMS.bat.
  * Forgot the administrator password ........ run SET_ADMIN_PASSWORD.bat and type the same username (it resets it).
  * A user is locked out (5 wrong passwords) .. wait 15 minutes, or sign in as administrator:
                                                 Administration > Users > open the user > Set password & unlock.
  * Windows asks whether to allow Python ..... tick Private networks and press Allow. Never Cancel
                                                 (Cancel blocks it; OPEN_FIREWALL_ONCE.bat repairs that).
  * Copy from USB looks incomplete ............ CHECK_COPY.bat tells which files are missing.

UPDATING TO A NEW KIT (never copy a new kit over C:\CTDMS)
  1. Run STOP_CTDMS.bat, then rename the folder C:\CTDMS to C:\CTDMS_old
  2. Copy the new kit folder to C:\CTDMS
  3. From C:\CTDMS_old copy the folder  data  and the file  .env  into the new C:\CTDMS
  4. START_CTDMS.bat.  Keep C:\CTDMS_old until the next day went well.
  A new kit carries no database on purpose: your records live only in the data folder.

WHERE THINGS ARE
  data\ctdms.db      the database (all your records)      data\backups   the backups
  data\logs          the program log                      .env           the program settings (created on first start)
  Never delete the data folder. Never run two copies of CTDMS on the same PC.
