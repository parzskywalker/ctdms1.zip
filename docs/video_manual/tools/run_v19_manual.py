"""v1.9 user-manual videos (silent, English captions + Hinglish voice-over script), recorded on SCRATCH databases.

  python run_v19_manual.py <v1|v2|v3|v4> <dry|record> [--noprep]

Run with the video venv (Playwright). The app runs from the project's .venv on a copy of the base practice database in
scratchpad\\manual_v19\\<video>; never the owner's data folder or the live server. Passwords are typed into masked fields."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v19lib as L  # noqa: E402
from v19lib import V19, Work  # noqa: E402

KEY, MODE = sys.argv[1], sys.argv[2]
PORTS = {"v1": 8041, "v2": 8042, "v3": 8043, "v4": 8044}
NAMES = {"v1": ("01_Poora_Flow", "CTDMS 1.9 - The whole flow, from invoice to delivery"),
         "v2": ("02_Depot_Manager", "CTDMS 1.9 - Depot Manager: full manual"),
         "v3": ("03_Dispatch_Operator", "CTDMS 1.9 - Dispatch Operator"),
         "v4": ("04_Guard_Phone", "CTDMS 1.9 - Gate staff: checking the gate pass on the phone")}
W = Work(KEY, PORTS[KEY])
TODAY = time.strftime("%Y-%m-%d")
CRED = W.creds


def prep():
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "v19_prep.py"), KEY], capture_output=True, text=True)
    print(r.stdout[-600:], r.stderr[-1500:])
    assert r.returncode == 0, "prep failed"


def login_as(d: V19, who: str, en: str, hi: str, shown: str | None = None):
    """Caption, then sign in (masked password)."""
    d.say(en, hi)
    user, pw = CRED[who]
    d.sign_in(user, pw)


def menu_tour(d: V19, items: list[str], group_hint=None):
    for it in items:
        loc = d.pg.locator(f'aside.side a:has-text("{it}")').first
        if loc.count():
            d._move(loc)
            d.pause(0.45)
            d._unhl(loc)


# =============================================================================================== VIDEO 3: Dispatch Operator
def video_operator(d: V19):
    d.card("Dispatch Operator", "CTDMS 1.9 - your daily work, screen by screen",
           "Namaste. Yeh video Dispatch Operator ke liye hai. Hum dekhenge ki invoice kaise aate hain, labels kaise print hote hain, "
           "gate pass kaise banta hai, aur LR aur delivery kaise bhari jaati hai. Sab data practice ka hai.")
    d.go("/login")
    d.say("Open CTDMS in the browser and sign in with your own username and password.",
          "Browser mein CTDMS kholiye. Apna username likhiye, aur apna password. Password screen par dots mein dikhta hai, kisi ko dikhta nahi.")
    user, pw = CRED["op"]
    d.sign_in(user, pw)
    d.say("After sign-in the left menu shows only the screens your role may use. Operations is first, then the Dispatch desk.",
          "Sign in ke baad left menu mein sirf aapke kaam ki screens dikhti hain. Sabse upar Operations hai, uske neeche Dispatch desk.")
    menu_tour(d, ["Add Invoice", "Invoice Import", "Invoice Register", "Labels to Print", "Planned Loads", "LR Pending",
                  "Deliveries Pending", "Track Shipment"])
    d.card("1. Operations - invoices", "Invoice Import and Add Invoice",
           "Pehla kaam: invoices system mein daalna. Do tareeke hain. Ek, ERP ki file import karna. Do, ek invoice haath se daalna.")

    d.nav("Invoice Import")
    d.say("Operations > Invoice Import. Drop the ERP export file here: CTDMS reads the column headings by itself.",
          "Operations mein Invoice Import kholiye. Yahan ERP se nikali hui file daalte hain. Columns ke naam dekh kar CTDMS khud samajh leta hai.")
    d.upload('main form[action="/imports/quick"] input[type=file]', str(W.dir / "files" / "Invoices_today.csv"))
    d.say("Choose the file, then press Import.",
          "File chuniye, aur Import button dabaiye.")
    d.click('main form[action="/imports/quick"] button:has-text("Import")')
    d.say("The result says how many invoices were imported. Rows with a problem are kept aside for correction, nothing is lost.",
          "Result mein likha aata hai kitne invoice import hue. Jis row mein galti ho, woh alag rakhi jaati hai, kuch khota nahi.")
    d.nav("Invoice Register")
    d.say("Operations > Invoice Register lists every invoice. The new invoices, with their area, company and cartons, are at the top.",
          "Invoice Register mein saare invoice dikhte hain. Naye invoice upar hain, unka area, company aur cartons ke saath.")
    d.pg.mouse.wheel(0, 120)

    d.nav("Add Invoice")
    d.say("Operations > Add Invoice: for an invoice that is not in the ERP file. Choose the customer - type a few letters and press Enter.",
          "Add Invoice us invoice ke liye hai jo file mein nahi hai. Customer chuniye. Kuch akshar likhiye aur Enter dabaiye.")
    d.select("customer_id", "ARPIT PHARMA BILASPUR")
    d.select("company_id", "TORRENT")
    d.select("cfa_id", "Guma Depot")
    d.say("Type the invoice number, the amount and the number of cartons. The date is today.",
          "Invoice number likhiye, amount likhiye, aur cartons ki ginti. Date aaj ki hi rehti hai.")
    d.fill("invoice_no", "GM/2627/0485")
    d.fill("sales_value", "36400")
    d.fill("boxes", "5")
    d.say("Press Add invoice (F6). The invoice is saved and the form is ready for the next one.",
          "Add invoice dabaiye, ya keyboard par F6. Invoice save ho gaya, aur form agle invoice ke liye khali ho gaya.")
    d.click('main button:has-text("Add invoice")')

    d.card("2. Labels to Print", "One label for every carton",
           "Dusra kaam: har invoice ke cartons ke labels print karna. Label ke bina gaadi mein maal nahi chadhta.")
    d.nav("Labels to Print")
    d.say("Dispatch desk > Labels to Print. The carton count is already filled from the invoice. Count the packed cartons.",
          "Labels to Print mein har invoice ke saamne cartons ki ginti pehle se bhari hoti hai. Packed cartons gin kar milaiye.")
    cnt = d.L('main input[name="count"]')
    d._move(cnt)
    cnt.click()
    d.say("If the count is right, press Enter. The label sheet opens: one label for every carton, with a barcode.",
          "Ginti sahi hai to Enter dabaiye. Label sheet khulti hai. Har carton ka ek label, aur us par barcode.")
    d.key("Enter", after=1.4)
    d.say("Print the sheet and stick one label on each carton. Then confirm: press Y if all labels printed well, N to print again.",
          "Sheet print kijiye, aur har carton par ek label chipkaiye. Phir confirm kijiye. Sab label theek chhape to Y, nahi to N.")
    d.key("y", show="Y", after=1.4)
    d.say("The next invoice is ready. Repeat for every invoice. A different carton count needs the Depot Manager - call him.",
          "Ab agla invoice. Har invoice ke liye yahi karna hai. Agar ginti invoice se alag hai, to Depot Manager ko bulaiye. Woh hi alag ginti confirm kar sakte hain.")
    for _ in range(2):
        d._move(d.L('main input[name="count"]'))
        d.key("Enter", after=0.8)
        d.key("y", show="Y", after=0.9)

    d.card("3. Planned Loads", "The Depot Manager plans the vehicle, you make the gate pass",
           "Teesra kaam: gate pass banana. Depot Manager gaadi ka plan banate hain. Aap us plan se gate pass banate aur print karte hain.")
    d.nav("Planned Loads")
    d.say("Dispatch desk > Planned Loads. Each plan is one vehicle: who carries, how many invoices and cartons.",
          "Planned Loads mein har plan ek gaadi ka hai. Kaun le ja raha hai, kitne invoice, kitne cartons.")
    d.click('main a:has-text("#")')
    d.say("Open a plan. Check the invoices and the vehicle. You cannot change them here - if something is wrong, the Depot Manager cancels the plan.",
          "Plan kholiye. Invoice aur gaadi dekh lijiye. Yahan kuch badal nahi sakte. Kuch galat ho to Depot Manager plan cancel karke naya banate hain.")
    d.say("Press Make gate pass (F8).",
          "Make gate pass dabaiye, ya keyboard par F8.")
    d.click('main button:has-text("Make gate pass")')
    slip_url = d.pg.url
    d.say("The gate pass is made, with its number, the invoices, the cartons and the charges. It still must be printed.",
          "Gate pass ban gaya. Uska number, invoice, cartons aur kharcha yahan dikhta hai. Ab ise print karna zaroori hai.")
    d.same_tab()
    d.say("Press Print gatepass (F8). The goods may leave only with this printed gate pass.",
          "Print gatepass dabaiye. Is printed gate pass ke bina maal gate se bahar nahi jata.")
    d.click('main button:has-text("Print gatepass")')
    d.pg.mouse.wheel(0, 250)
    d.say("The printed gate pass: Tulsyan name and logo, the barcode the guard scans, the invoices and the charge.",
          "Yeh printed gate pass hai. Tulsyan ka naam aur logo, guard jo barcode scan karega, invoice, aur kharcha.")
    d.go(slip_url)
    d.say("Back on the gate pass: the Garage Pawati slips are listed. One slip for each garage that receives goods from the local auto.",
          "Gate pass par wapas aaiye. Yahan Garage Pawati ki slips dikhti hain. Jis garage mein local auto maal chhodega, uski ek slip.")
    d.same_tab()
    d.say("Press Print Pawatis. The Pawati travels with the auto and stays with the garage.",
          "Print Pawatis dabaiye. Pawati auto ke saath jaati hai, aur garage ke paas rehti hai.")
    d.click('main button:has-text("Print Pawatis")')
    d.say("The Pawati has the garage, the customer, the invoice and the cartons. Give it to the driver with the gate pass.",
          "Pawati mein garage, customer, invoice aur cartons likhe hain. Driver ko gate pass ke saath yeh bhi dijiye.")
    d.go("/pawati")
    d.say("Dispatch desk > Garage Pawati is the depot's register of all Pawatis. Press Received when the garage confirms.",
          "Garage Pawati screen depot ka register hai. Garage maal le le to Received dabaiye.")

    d.card("4. After the vehicle has left", "LR Pending, Deliveries Pending, Track Shipment",
           "Chautha kaam: gaadi nikalne ke baad. Transporter LR number baad mein deta hai. Woh yahan likhna hai. Phir delivery ka hisaab.")
    d.nav("LR Pending")
    d.say("Dispatch desk > LR Pending: consignments that left but have no LR number yet. The oldest are on top.",
          "LR Pending mein woh consignment hain jo nikal chuke hain par unka LR number abhi nahi aaya. Sabse purane upar hain.")
    lr = d.pg.locator('main input[name^="lr_"]')
    d.say("Type the LR number the transporter gave and its date. Fill every row you have; empty rows are left alone.",
          "Transporter ne jo LR number diya hai woh likhiye, aur uski date. Jitni rows ka LR mila hai utni bhariye. Khali rows chhod dijiye.")
    for i, (no, dt) in enumerate((("OE-77310", "2026-11-22"), ("OE-77311", "2026-11-22"))):
        d._move(lr.nth(i))
        lr.nth(i).click()
        lr.nth(i).press_sequentially(no, delay=60 if d.record else 0)
        dloc = d.pg.locator('main input[name^="lrdate_"]').nth(i)
        dloc.fill(dt)
    d.say("Press Save LR numbers (F6). Only the filled rows are saved.",
          "Save LR numbers dabaiye, ya F6. Sirf bhari hui rows save hoti hain.")
    d.click('main button:has-text("Save LR numbers")')
    d.nav("Deliveries Pending")
    d.say("Dispatch desk > Deliveries Pending: consignments that left the gate and have no proof of delivery yet. Late ones are marked.",
          "Deliveries Pending mein woh consignment hain jo gate se nikle par jinka delivery proof (POD) abhi nahi aaya. Der wale alag dikhte hain.")
    row = 'main tr:has-text("GM/2627/0457")'
    d._move(d.L(row + ' input[name="pick"]'))
    d.L(row + ' input[name="pick"]').check()
    rcv = d.L(row + ' input[name^="rcv_"]')
    d._move(rcv)
    rcv.click()
    rcv.press_sequentially("Goyal (shop owner)", delay=50 if d.record else 0)
    d.say("When the signed POD copy comes back, tick the row, write who received it, and press Mark ticked as delivered.",
          "Jab signed POD copy wapas aaye, row par tick lagaiye, receive karne wale ka naam likhiye, aur Mark ticked as delivered dabaiye.")
    d.click('main button:has-text("Mark ticked as delivered")')
    d.nav("Track Shipment")
    d.say("Dispatch desk > Track Shipment: type an invoice number, LR number or gate pass number.",
          "Track Shipment mein invoice number, LR number ya gate pass number likhiye.")
    d.type_in('main input[name="q"]', "GM/2627/0457", enter=True)
    d.say("The answer shows where the consignment is. Open it to see every step with date and time.",
          "Jawab mein dikhta hai ki consignment kahan hai. Kholiye to har step date aur time ke saath dikhta hai.")
    d.click('main table a.mono, main table a:has-text("GM/2627")')
    d.pg.mouse.wheel(0, 200)
    d.say("The timeline: imported, labelled, gate pass, left the gate, LR received, delivered. This is how you answer a customer's call.",
          "Timeline mein sab dikhta hai. Invoice aaya, label bane, gate pass bana, gaadi nikli, LR aaya, delivery hui. Customer ka phone aaye to yahin se jawab dijiye.")
    d.card("Your day in five steps", "Invoices - Labels - Planned Loads - LR Pending - Deliveries Pending",
           "Yeh aapka poora din hai. Pehle invoice, phir labels, phir Planned Loads se gate pass, phir LR aur delivery. Dhanyavaad.")


# ------------------------------------------------------------------------------------------------ shared pieces
def tick(d: V19, no: str):
    b = d.pg.locator(f'main input[name="ids"][aria-label="Load {no}"]')
    d._move(b)
    b.check()
    d.pause(0.5)


def carry_by_garage(d: V19, transporter: str, auto: str, charge: str):
    """Vehicle = Transporter / garage (LR), with a local auto taking the goods to the garage (per trip)."""
    d.radio("mode", "TRANSPORTER")
    d.select("transporter_id", transporter)
    d.radio("pickup_mode", "OUTSIDE_AUTO")
    d.select("outside_auto_id", auto)
    if d.pg.locator("#gp-method-box").is_visible():
        d.radio("auto_method", "PER_TRIP")
    if d.pg.locator("#gp-trip").is_visible():
        d.fill("trip_charge", charge)
        d.pg.dispatch_event("#gp-trip", "input")


def label_rows(d: V19, n: int):
    for _ in range(n):
        d._move(d.L('main input[name="count"]'))
        d.key("Enter", after=0.8)
        d.key("y", show="Y", after=0.9)


def slip_codes(slip_id: int):
    no = W.sql("select dispatch_no from dispatch_slips where id=?", (slip_id,))[0][0]
    cases = [r[0] for r in W.sql("select case_code from dispatch_cases where slip_id=? and voided_at is null order by id", (slip_id,))]
    paw = [f"PW{r[0]}" for r in W.sql("select id from garage_pawatis where slip_id=? and status!='CANCELLED' order by id", (slip_id,))]
    return no, cases, paw


def scan(d: V19, code: str, after=1.1):
    """Type a code into the scan box and press Enter, as a Bluetooth scanner does.
    APP PROBLEM (v1.8.7): app.js disables the form's submit button for 8 s after every submit (double-submit guard), and a
    disabled default button blocks Enter, so a second scan within 8 s is lost. The video re-enables the button first."""
    box = d.L("#code")
    d._move(box)
    box.click()
    box.fill("")
    box.press_sequentially(code, delay=25)
    d.pg.evaluate("() => document.querySelectorAll('#manual button').forEach(b => { b.disabled = false; })")
    box.press("Enter")
    time.sleep(0.8)  # the page answers in the background: never type the next code before it has answered
    d.pause(after)


def import_today(d: V19):
    d.nav("Invoice Import")
    d.say("Operations > Invoice Import. Drop the ERP export file here: CTDMS reads the column headings by itself.",
          "Operations mein Invoice Import kholiye. Yahan ERP se nikali hui file daalte hain. Columns ke naam dekh kar CTDMS khud samajh leta hai.")
    d.upload('main form[action="/imports/quick"] input[type=file]', str(W.dir / "files" / "Invoices_today.csv"))
    d.say("Choose the file, then press Import.", "File chuniye, aur Import button dabaiye.")
    d.click('main form[action="/imports/quick"] button:has-text("Import")')
    d.say("The result says how many invoices were imported. A row with a problem is kept aside for correction, nothing is lost.",
          "Result mein likha aata hai kitne invoice import hue. Jis row mein galti ho woh alag rakhi jaati hai, kuch khota nahi.")


def add_invoice(d: V19, no: str):
    d.nav("Add Invoice")
    d.say("Operations > Add Invoice: for an invoice that is not in the ERP file. Choose the customer - type a few letters and press Enter.",
          "Add Invoice us invoice ke liye hai jo file mein nahi hai. Customer chuniye. Kuch akshar likhiye aur Enter dabaiye.")
    d.select("customer_id", "ARPIT PHARMA BILASPUR")
    d.select("company_id", "TORRENT")
    d.select("cfa_id", "Guma Depot")
    d.say("Type the invoice number, the amount and the number of cartons. The date is today.",
          "Invoice number likhiye, amount likhiye, aur cartons ki ginti. Date aaj ki hi rehti hai.")
    d.fill("invoice_no", no)
    d.fill("sales_value", "36400")
    d.fill("boxes", "5")
    d.say("Press Add invoice (F6). The invoice is saved and the form is ready for the next one.",
          "Add invoice dabaiye, ya keyboard par F6. Invoice save ho gaya, aur form agle invoice ke liye khali ho gaya.")
    d.click('main button:has-text("Add invoice")')


def make_pass_from_plan(d: V19) -> int:
    """Operator: open the first plan, Make gate pass, print gate pass and Pawatis. Returns the slip id."""
    d.nav("Planned Loads")
    d.say("Dispatch desk > Planned Loads. Each plan is one vehicle: who carries, how many invoices and cartons.",
          "Planned Loads mein har plan ek gaadi ka hai. Kaun le ja raha hai, kitne invoice, kitne cartons.")
    d.click('main a:has-text("#")')
    d.say("Open the plan. Check the invoices and the vehicle. You cannot change them here - if something is wrong, the Depot Manager cancels the plan.",
          "Plan kholiye. Invoice aur gaadi dekh lijiye. Yahan kuch badal nahi sakte. Kuch galat ho to Depot Manager plan cancel karte hain.")
    d.say("Press Make gate pass (F8).", "Make gate pass dabaiye, ya keyboard par F8.")
    d.click('main button:has-text("Make gate pass")')
    slip_url = d.pg.url
    sid = int(slip_url.rstrip("/").split("/")[-1])
    d.say("The gate pass is made, with its number, the invoices, the cartons and the charges. It still must be printed.",
          "Gate pass ban gaya. Uska number, invoice, cartons aur kharcha yahan dikhta hai. Ab ise print karna zaroori hai.")
    d.same_tab()
    d.say("Press Print gatepass (F8). The goods may leave only with this printed gate pass.",
          "Print gatepass dabaiye. Is printed gate pass ke bina maal gate se bahar nahi jata.")
    d.click('main button:has-text("Print gatepass")')
    d.pg.mouse.wheel(0, 250)
    d.say("The printed gate pass: Tulsyan name and logo, the barcode the guard scans, the invoices and the charge.",
          "Yeh printed gate pass hai. Tulsyan ka naam aur logo, guard jo barcode scan karega, invoice, aur kharcha.")
    d.go(slip_url)
    d.same_tab()
    d.say("Back on the gate pass: press Print Pawatis. One Garage Pawati for each garage; it travels with the auto and stays with the garage.",
          "Gate pass par wapas aaiye. Print Pawatis dabaiye. Jis garage mein auto maal chhodega uski ek Pawati. Woh auto ke saath jaati hai aur garage ke paas rehti hai.")
    d.click('main button:has-text("Print Pawatis")')
    d.say("The Pawati has the garage, the customer, the invoice and the cartons. Give it to the driver with the gate pass.",
          "Pawati mein garage, customer, invoice aur cartons likhe hain. Driver ko gate pass ke saath yeh bhi dijiye.")
    return sid


def lr_for_slip(d: V19, sid: int):
    d.nav("LR Pending")
    d.say("Dispatch desk > LR Pending: consignments that left but have no LR number yet.",
          "LR Pending mein woh consignment hain jo nikal chuke hain par jinka LR number abhi nahi aaya.")
    rows = d.pg.locator(f'main input[name^="lr_{sid}_"]')
    d.say("Type the LR number the transporter gave and its date. Press Save LR numbers (F6).",
          "Transporter ne jo LR number diya hai woh likhiye, aur uski date. Phir Save LR numbers dabaiye, ya F6.")
    for i in range(rows.count()):
        d._move(rows.nth(i))
        rows.nth(i).click()
        rows.nth(i).press_sequentially(f"OE-8820{i}", delay=60 if d.record else 0)
        name = rows.nth(i).get_attribute("name").replace("lr_", "lrdate_", 1)
        d.pg.locator(f'main input[name="{name}"]').fill(TODAY)
    d.click('main button:has-text("Save LR numbers")')


# =============================================================================================== VIDEO 1: the whole flow
def video_flow(d: V19):
    d.card("The whole flow", "From invoice to delivery - who does what",
           "Namaste. Is video mein hum poora raasta dekhenge. Invoice aata hai, labels bante hain, gaadi ka plan banta hai, gate pass nikalta hai, guard phone se check karta hai, aur LR aur delivery bharte hain.")
    d.go("/login")
    d.say("Step 1 - the Dispatch Operator signs in and imports today's invoices.",
          "Pehla kaam Dispatch Operator ka. Woh apne username aur password se sign in karta hai. Password dots mein dikhta hai.")
    d.sign_in(*CRED["op"])
    import_today(d)
    d.nav("Labels to Print")
    d.say("Step 2 - Labels to Print: count the cartons, press Enter, then Y when the labels have printed. One label for every carton.",
          "Doosra kaam labels. Cartons gin kar milaiye, Enter dabaiye, label sheet print kijiye, aur Y dabaiye. Har carton ka ek label.")
    d._move(d.L('main input[name="count"]'))
    d.key("Enter", after=1.4)
    d.key("y", show="Y", after=1.2)
    d.say("Repeat for every invoice. The labelled invoices now move to Ready to Load.",
          "Har invoice ke liye yahi karna hai. Label lagte hi invoice Ready to Load mein chale jaate hain.")
    label_rows(d, 3)
    d.sign_out()

    d.go("/login")
    d.say("Step 3 - the Depot Manager signs in. Only he plans the vehicles.",
          "Teesra kaam Depot Manager ka. Woh apne naam se sign in karta hai. Gaadi ka plan sirf woh banata hai.")
    d.sign_in(*CRED["dm"])
    d.nav("Ready to Load")
    d.say("Ready to Load: invoices that are fully labelled and not yet on a vehicle. Tick the invoices that go in one vehicle.",
          "Ready to Load mein woh invoice hain jinke saare label lag chuke hain. Jo invoice ek gaadi mein jayenge un par tick lagaiye.")
    tick(d, "GM/2627/0481")
    tick(d, "GM/2627/0474")
    d.say("Press Make gatepass for this vehicle.", "Make gatepass for this vehicle dabaiye.")
    d.btn("Make gatepass for this vehicle")
    d.say("Choose Transporter / garage (LR). The goods go to the transporter's garage by a local auto: choose the auto and the trip charge.",
          "Gaadi mein Transporter / garage (LR) chuniye. Maal local auto se garage tak jayega. Auto chuniye aur trip ka kharcha likhiye.")
    carry_by_garage(d, "OSHO EXPRESS", "NANDKUMAR AUTO", "700")
    d.say("Press Save as plan (F9). The plan waits for the operator. The invoices leave Ready to Load.",
          "Save as plan dabaiye, ya F9. Plan operator ke liye wait karta hai. Ab yeh invoice dobara kisi aur gaadi mein nahi ja sakte.")
    d.press("#gp-plan")
    d.sign_out()

    d.go("/login")
    d.say("Step 4 - the operator signs in again and makes the gate pass from the plan.",
          "Chautha kaam. Operator dobara sign in karta hai, aur plan se gate pass banata hai.")
    d.sign_in(*CRED["op"])
    sid = make_pass_from_plan(d)
    d.go("/pawati")
    d.sign_out()

    no, cases, paw = slip_codes(sid)
    d.go("/login")
    d.say("Step 5 - at the gate, the guard signs in on his phone.",
          "Paanchva kaam. Gate par guard apne phone se sign in karta hai.")
    d.sign_in(*CRED["guard"])
    d.nav("Check Gatepass (phone)")
    d.say("Check Gatepass: scan the gate pass barcode, then every carton, then the Pawati.",
          "Check Gatepass mein pehle gate pass ka barcode scan kijiye, phir har carton, phir Pawati.")
    scan(d, no)
    for c in cases[:3]:
        scan(d, c, after=0.8)
    for c in cases[3:]:
        scan(d, c, after=0.3)
    for p in paw:
        scan(d, p)
    d.say("Scan the gate pass once more: all cartons counted, the screen says DISPATCHED. The goods may leave.",
          "Gate pass ek baar phir scan kijiye. Saare cartons gin liye, screen par DISPATCHED aata hai. Ab gaadi nikal sakti hai.")
    scan(d, no, after=1.5)
    d.sign_out()

    d.go("/login")
    d.say("Step 6 - after the vehicle has left, the operator fills the LR number the transporter sends.",
          "Chhatha kaam. Gaadi nikalne ke baad operator LR number likhta hai jo transporter bhejta hai.")
    d.sign_in(*CRED["op"])
    lr_for_slip(d, sid)
    d.nav("Deliveries Pending")
    d.say("Deliveries Pending: consignments that left but have no signed delivery proof yet. When the POD comes back, mark it delivered.",
          "Deliveries Pending mein woh consignment hain jinka signed delivery proof (POD) abhi nahi aaya. POD aate hi delivered mark kijiye.")
    d.card("Who does what", "Operator: invoices, labels, gate pass, LR, delivery - Depot Manager: plans the vehicle - Guard: checks at the gate",
           "Yaad rakhiye. Operator invoice, labels, gate pass, LR aur delivery bharta hai. Depot Manager gaadi ka plan banata hai. Guard gate par phone se check karta hai. Dhanyavaad.")


# =============================================================================================== VIDEO 2: Depot Manager
def video_dm(d: V19):
    d.card("Depot Manager", "CTDMS 1.9 - the full manual",
           "Namaste. Yeh video Depot Manager ke liye hai. Hum har screen dekhenge: invoice, labels, gaadi ka plan, gate pass, aur baaki ki screens.")
    d.go("/login")
    d.say("Open CTDMS in the browser and sign in with your own username and password.",
          "Browser mein CTDMS kholiye. Apna username aur password likhiye. Password dots mein dikhta hai.")
    d.sign_in(*CRED["dm"])
    d.say("The left menu shows only the screens your role may use. Operations is above the Dispatch desk.",
          "Left menu mein sirf aapke kaam ki screens dikhti hain. Operations sabse upar hai, Dispatch desk uske neeche.")
    menu_tour(d, ["Add Invoice", "Invoice Import", "Invoice Register", "Freight Exceptions", "Labels to Print", "Ready to Load",
                  "Planned Loads", "Dispatch Exceptions", "Garage Pawati", "LR Pending", "Deliveries Pending", "Approvals"])

    d.say("The menu has four groups for you: Operations for invoices, Dispatch desk for labels, vehicles and gate passes, Approvals, and Operational Reports at the bottom.",
          "Menu mein chaar hisse hain. Operations, jahan invoice aate hain. Dispatch desk, jahan labels, gaadi aur gate pass hain. Approvals. Aur sabse neeche Operational Reports.")
    d.say("Keyboard first: F3 opens a quick search of every screen, F4 searches an invoice, F1 shows help. The key row at the bottom of the screen tells you the keys of the page you are on.",
          "Yeh system keyboard se bhi chalta hai. F3 dabaiye to koi bhi screen jaldi mil jati hai. F4 se invoice dhundhiye. F1 madad. Neeche ki patti mein is page ki keys likhi rehti hain.")
    d.card("1. Operations", "Invoice Import, Add Invoice, Invoice Register, Freight Exceptions",
           "Pehla hissa: Operations. Yahan invoice system mein aate hain.")
    import_today(d)
    add_invoice(d, "GM/2627/0485")
    d.nav("Invoice Register")
    d.say("Operations > Invoice Register lists every invoice with its area, company, cartons and state. The new ones are at the top.",
          "Invoice Register mein saare invoice hain, area, company, cartons aur state ke saath. Naye invoice upar hain.")
    d.pg.mouse.wheel(0, 120)
    d.nav("Dispatch Register")
    d.say("Operations > Dispatch Register: the list of dispatches, with the invoices and the vehicle of each one.",
          "Dispatch Register mein saare dispatch hain, har dispatch ke invoice aur gaadi ke saath. Kuch dhundhna ho to yahin se dhundhiye.")
    d.nav("Freight Exceptions")
    d.say("Operations > Freight Exceptions: invoices where the freight rate is missing or looks wrong. Fix the rate here, before dispatch.",
          "Freight Exceptions mein woh invoice hain jinka freight rate nahi mila ya galat lagta hai. Dispatch se pehle yahan theek kijiye.")

    d.card("2. Labels to Print", "One label per carton - and the carton-count exception",
           "Dusra hissa: labels. Har carton ka ek label. Aur agar ginti invoice se alag hai to sirf Depot Manager use confirm kar sakta hai.")
    d.nav("Labels to Print")
    d.say("Dispatch desk > Labels to Print. The carton count is filled from the invoice. Count the packed cartons.",
          "Labels to Print mein cartons ki ginti invoice se bhari hoti hai. Packed cartons gin kar milaiye.")
    d._move(d.L('main input[name="count"]'))
    d.key("Enter", after=1.4)
    d.say("Print the sheet, stick one label on each carton, press Y.",
          "Sheet print kijiye, har carton par ek label chipkaiye, aur Y dabaiye.")
    d.key("y", show="Y", after=1.2)
    row = 'main tr:has-text("GM/2627/0482")'
    d.say("If the packed cartons differ from the invoice, only you can confirm: type the new count and the reason.",
          "Agar packed cartons invoice se alag hain, to sirf aap confirm kar sakte hain. Nayi ginti aur wajah likhiye.")
    cnt = d.L(row + ' input[name="count"]')
    d._move(cnt)
    cnt.click()
    cnt.press("Control+a")
    cnt.press_sequentially("6", delay=80 if d.record else 0)
    rs = d.L(row + ' input[name="reason"]')
    d._move(rs)
    rs.click()
    rs.press_sequentially("Party asked for one extra carton", delay=40 if d.record else 0)
    d.say("Press Enter. This is an exception: it is listed on the gate pass, signed by you, and photographed by the guard.",
          "Enter dabaiye. Yeh exception hai. Yeh gate pass par likha jata hai, aap us par sign karte hain, aur guard uski photo leta hai.")
    d.key("Enter", after=1.4)
    d.key("y", show="Y", after=1.2)
    label_rows(d, 2)

    d.card("3. Ready to Load", "Plan the vehicle",
           "Teesra hissa: gaadi ka plan. Yeh sirf Depot Manager ka kaam hai.")
    d.nav("Ready to Load")
    d.say("Ready to Load: fully labelled invoices that are on no vehicle yet, grouped by area. Tick the invoices for one vehicle.",
          "Ready to Load mein woh invoice hain jinke saare label lag chuke hain. Area ke hisaab se rakhe hain. Ek gaadi ke invoice par tick lagaiye.")
    d.pg.mouse.wheel(0, 260)
    d.say("Each block is one area, with its invoices and cartons. The Age column shows how many days an invoice has waited: send the old ones first.",
          "Har block ek area hai, uske invoice aur cartons ke saath. Age mein likha hai invoice kitne din se ruka hai. Purane invoice pehle bhejiye.")
    d.pg.mouse.wheel(0, -260)
    tick(d, "GM/2627/0481")
    tick(d, "GM/2627/0474")
    d.say("Press Make gatepass for this vehicle. Space ticks an invoice from the keyboard too.",
          "Make gatepass for this vehicle dabaiye. Keyboard se Space dabane par bhi tick lagta hai.")
    d.btn("Make gatepass for this vehicle")
    d.say("This is the vehicle screen. On the left are your invoices with their cartons. You can correct the cartons of an invoice here, with a reason.",
          "Yeh gaadi ki screen hai. Left mein aapke invoice aur unke cartons hain. Cartons yahan badal bhi sakte hain, wajah ke saath.")
    d.say("Who carries the goods? First look at Local auto to the customer (no LR). It is only for local deliveries.",
          "Maal kaun le jayega? Pehle Local auto to the customer (no LR) dekhiye. Yeh sirf local delivery ke liye hai.")
    d.radio("mode", "OUTSIDE_AUTO")
    d.pause(1.0)
    d.say("The warning: no garage, no Pawati and no LR. Outstation customers must not go this way.",
          "Yeh warning dekhiye. Isme garage nahi, Pawati nahi, aur LR bhi nahi. Bahar ke shehar ke customer is tareeke se nahi bhejte.")
    d.say("For outstation goods choose Transporter / garage (LR). Choose the transporter, then the local auto that takes the goods to the garage.",
          "Bahar ke shehar ke liye Transporter / garage (LR) chuniye. Transporter chuniye, phir woh local auto jo maal garage tak le jayega.")
    carry_by_garage(d, "OSHO EXPRESS", "NANDKUMAR AUTO", "700")
    d.say("Two buttons. Generate gatepass (F8) makes the gate pass yourself. Save as plan (F9) leaves it for the operator. Now: Save as plan.",
          "Do button hain. Generate gatepass, F8, gate pass aap khud banate hain. Save as plan, F9, operator ke liye chhodta hai. Abhi Save as plan dabaiye.")
    d._move(d.L("#gp-submit"))
    d.pause(1.0)
    d.press("#gp-plan")

    d.card("4. Planned Loads", "Cancel a plan, or let the operator make the gate pass",
           "Chautha hissa: Planned Loads. Saare saved plan yahan hain.")
    d.nav("Planned Loads")
    d.say("Dispatch desk > Planned Loads: every saved plan, with the vehicle, the invoices and who planned it.",
          "Planned Loads mein saare plan hain, gaadi, invoice aur kisne banaya, sab ke saath.")
    d.click('main a:has-text("#")')
    d.say("Plan wrong, vehicle broke down? Type a reason and press Cancel plan. The invoices go back to Ready to Load.",
          "Plan galat hai, ya gaadi kharab ho gayi? Wajah likhiye aur Cancel plan dabaiye. Invoice wapas Ready to Load mein aa jaate hain.")
    d.type_in('main input[name="reason"]', "Vehicle broke down - plan again")
    d.click('main button:has-text("Cancel plan")')

    d.nav("Ready to Load")
    d.say("Now make a gate pass yourself, for an urgent vehicle. Tick the invoice, choose the vehicle, press Generate gatepass (F8).",
          "Ab urgent gaadi ke liye gate pass khud banaiye. Invoice par tick lagaiye, gaadi chuniye, Generate gatepass dabaiye, ya F8.")
    tick(d, "GM/2627/0481")
    d.btn("Make gatepass for this vehicle")
    carry_by_garage(d, "OSHO EXPRESS", "NANDKUMAR AUTO", "700")
    d.press("#gp-submit")
    d.say("The gate pass is made. It still must be printed. Until it has gone out at the gate you can void it.",
          "Gate pass ban gaya. Ab ise print karna hai. Jab tak woh gate se bahar nahi gaya, aap use void kar sakte hain.")
    d.pg.mouse.wheel(0, 300)
    d.say("The gate pass shows its invoices, the cartons, the freight, the local auto charge, the total charged and what was paid. Check these before it is printed.",
          "Gate pass par invoice, cartons, freight, local auto ka kharcha, total aur jo paisa diya woh sab dikhta hai. Print se pehle yeh dekh lijiye.")
    d.pg.mouse.wheel(0, 400)
    d.say("Void a gate pass that has not gone out: type the reason and press Void. The invoices go back to Ready to Load.",
          "Jo gate pass abhi bahar nahi gaya use void kar sakte hain. Wajah likhiye aur Void dabaiye. Invoice wapas Ready to Load mein aa jaate hain.")
    d.type_in("#void-reason", "Wrong vehicle - cancelled before loading")
    vb = d.L('main button:has-text("Void")')
    d._move(vb)
    d.sync_lead(2.5)
    d.pg.evaluate("() => window.__pulse && window.__pulse()")
    vb.click()
    d.pause(1.2)
    if d.pg.locator("#kb-ask").count():  # the Yes / No box: answer with the key shown in its hint
        d.key("y", show="Y", after=0.5)
    d.pg.wait_for_load_state("networkidle")
    d.pause(0.8)
    d.say("The gate pass is marked VOID. If the guard scans it, his phone shows VOID in red.",
          "Gate pass par VOID likh gaya. Guard ise scan karega to uske phone par STOP aayega.")

    d.card("5. After dispatch", "Garage Pawati, LR Pending, Dispatch Exceptions, Deliveries Pending",
           "Paanchva hissa: dispatch ke baad ki screens.")
    d.nav("Garage Pawati")
    d.say("Dispatch desk > Garage Pawati: every Pawati, printed, and received by the garage or not yet.",
          "Garage Pawati mein saari Pawati hain. Print hui ya nahi, garage ne receive ki ya nahi.")
    d.nav("LR Pending")
    d.say("LR Pending: consignments that left without an LR number. The operators fill them; you watch the old ones.",
          "LR Pending mein woh consignment hain jinka LR number nahi aaya. Operator bharte hain, aap purane wale par nazar rakhiye.")
    d.nav("Dispatch Exceptions")
    d.say("Dispatch Exceptions lists every exception: a changed carton count, an urgent trip, a gate exception. Each one has its reason.",
          "Dispatch Exceptions mein har exception hai. Badli hui ginti, urgent trip, gate ki gadbad. Har ek ki wajah ke saath.")
    d.nav("Deliveries Pending")
    d.say("Deliveries Pending: consignments without a signed delivery proof. Late ones are marked.",
          "Deliveries Pending mein woh consignment hain jinka signed delivery proof nahi aaya. Der wale alag dikhte hain.")

    d.nav("Track Shipment")
    d.say("Track Shipment: type an invoice number, LR number or gate pass number to see where the consignment is, and every step with date and time.",
          "Track Shipment mein invoice number, LR number ya gate pass number likhiye. Consignment kahan hai aur har step ki date aur time dikhta hai.")
    d.nav("Gatepasses")
    d.say("Gatepasses: the list of all gate passes, printed or not, gone out or void. Open any one to see its full story.",
          "Gatepasses mein saare gate pass hain. Print hue ya nahi, bahar gaye ya void. Kisi ko kholiye to uski poori kahani dikhti hai.")
    d.nav("Pending Dispatch")
    d.say("Pending Dispatch: invoices that are still waiting to be sent, with their age. A long list here means vehicles are not being planned.",
          "Pending Dispatch mein woh invoice hain jo abhi bheje nahi gaye, unki umar ke saath. Yahan list lambi ho to gaadi ka plan nahi ban raha.")
    d.card("6. Approvals and reports", "Approvals, Gatepass Register",
           "Aakhri hissa: Approvals aur report.")
    d.nav("Approvals")
    d.say("Approvals: new freight rates and overrides wait here for you. A rate counts only after you approve it.",
          "Approvals mein naye freight rate aur overrides aapka intezaar karte hain. Rate tabhi lagta hai jab aap approve karte hain.")
    d.nav("Approval History")
    d.say("Approval History: everything that was approved or refused, by whom, and when.",
          "Approval History mein jo bhi approve ya refuse hua, kisne kiya aur kab, sab likha hai.")
    d.nav("Delivery Register")
    d.say("Operational Reports also has the Delivery Register and Dispatch Exceptions. All Reports lists every report; each can be sent to Excel or PDF.",
          "Operational Reports mein Delivery Register aur Dispatch Exceptions bhi hain. All Reports mein saari reports hain. Har report Excel ya PDF mein jaa sakti hai.")
    d.nav("Gatepass Register")
    d.say("Reports > Gatepass Register: every gate pass of your depot, its state, the charges and the LR still missing. Excel and PDF are one click away.",
          "Gatepass Register mein aapke depot ke saare gate pass hain. State, kharcha, aur kaunsa LR baaki hai. Excel aur PDF bhi yahin se.")
    d.card("Your day", "Labels - Ready to Load - plan - gate pass - exceptions - approvals",
           "Yeh aapka din hai. Labels dekhiye, Ready to Load se gaadi ka plan banaiye, exceptions aur approvals dekhiye. Dhanyavaad.")


# =============================================================================================== VIDEO 4: the guard, on a phone
def video_guard(d: V19):
    info = json.loads((W.dir / "setup_info.json").read_text(encoding="utf-8"))
    d.card("Gate staff", "Checking the gate pass on the phone",
           "Namaste. Yeh video gate staff ke liye hai. Gaadi nikalne se pehle phone se gate pass check karna hai.")
    d.go("/login")
    d.say("Open CTDMS on the phone and sign in with your own username and password.",
          "Phone par CTDMS kholiye. Apna username aur password likhiye. Password dots mein dikhta hai.")
    d.sign_in(*CRED["guard"])
    d.nav("Check Gatepass (phone)")
    d.say("This is Check gatepass. The camera button scans a barcode. A Bluetooth scanner, or typing the code, does exactly the same.",
          "Yeh Check gatepass screen hai. Camera button barcode scan karta hai. Bluetooth scanner ya code type karna bhi bilkul wahi kaam karta hai.")
    d.say("On a real phone the camera opens by itself: point it at the barcode. This video runs on a computer, so the camera message is shown and the code is typed.",
          "Asli phone mein camera apne aap khulta hai. Barcode ke saamne le jaiye. Yeh video computer par hai, isliye camera ka message dikh raha hai aur code type kar rahe hain.")
    d.say("Step 1: scan the barcode on the printed GATE PASS. Here we type the code into the box and press Enter.",
          "Pehla step. Printed gate pass ka barcode scan kijiye. Yahan hum code box mein type karke Enter dabate hain.")
    scan(d, info["dispatch_no"], after=1.5)
    d.say("The screen shows the gate pass number and how many cartons to scan.",
          "Screen par gate pass ka number dikhta hai, aur kitne cartons scan karne hain.")
    d.say("Step 2: scan every carton label. Each good scan counts and beeps. A wrong or repeated label is refused.",
          "Doosra step. Har carton ka label scan kijiye. Sahi scan gina jaata hai aur beep karta hai. Galat ya dobara scan kiya label mana ho jata hai.")
    cs = info["cases"]
    for c in cs[:2]:
        scan(d, c, after=1.0)
    for c in cs[2:]:
        scan(d, c, after=0.35)
    d.pause(1.0)
    d.say("Step 3: scan each Garage Pawati. The list shows which Pawati is still left.",
          "Teesra step. Har Garage Pawati scan kijiye. List mein dikhta hai kaunsi Pawati baaki hai.")
    for p in info["pawati"]:
        scan(d, p, after=1.2)
    d.say("This gate pass has an exception, so the screen asks for a photo of the gate pass signed by the Depot Manager.",
          "Is gate pass par exception hai, isliye screen signed gate pass ki photo maangti hai. Us par Depot Manager ke sign hone chahiye.")
    snap = d.L("#snap")
    d._move(snap)
    d.pause(1.0)
    d.say("Press Take photo of signed gatepass and photograph the signed paper. (In this video a ready sample photo is used.)",
          "Take photo of signed gatepass dabaiye aur signed kagaz ki photo lijiye. Is video mein ek taiyar sample photo lagayi hai.")
    d.pg.evaluate("() => window.__pulse && window.__pulse()")
    d.pg.locator("#photo").set_input_files(str(W.dir.parent / "base" / "tiny_signed.jpg"))
    d.pause(1.5)
    d.say("Now scan the GATE PASS barcode once more. All cartons and Pawatis done: the screen says DISPATCHED.",
          "Ab gate pass ka barcode ek baar phir scan kijiye. Carton aur Pawati sab ho gaye, to screen par DISPATCHED likha aata hai. Gaadi nikal sakti hai.")
    scan(d, info["dispatch_no"], after=2.0)
    d.say("Next vehicle: press New gatepass. This gate pass was voided by the Depot Manager before loading.",
          "Agli gaadi. New gatepass dabaiye. Is gate pass ko Depot Manager ne loading se pehle void kar diya tha.")
    d.click("#restart", wait=False)
    scan(d, info["void_no"], after=2.0)
    d.say("A void gate pass shows a red message: VOID, the goods must not leave. The goods must not leave. Call the Depot Manager.",
          "Void gate pass par laal rang mein VOID likha aata hai, ki maal bahar nahi jana chahiye. Maal bahar nahi jana chahiye. Depot Manager ko phone kijiye.")
    d.say("Tip: in the phone's browser menu choose Add to Home screen. CTDMS then opens like an app, one touch from the home screen.",
          "Ek tip. Phone ke browser menu mein Add to Home screen chuniye. Phir CTDMS app ki tarah home screen se ek touch mein khulta hai.")
    d.card("Gate check in short", "Gate pass - cartons - Pawati - photo if exception - gate pass again = DISPATCHED",
           "Yaad rakhiye. Gate pass scan, cartons scan, Pawati scan, exception ho to photo, phir gate pass dobara scan. DISPATCHED aaye tabhi gaadi nikalegi. Dhanyavaad.")


# ------------------------------------------------------------------------------------------------ outputs
def write_outputs(vid, d: V19):
    OUT = L.OUTDIR
    OUT.mkdir(parents=True, exist_ok=True)
    name, title = NAMES[KEY]
    webm = Path(vid) if vid else sorted((W.dir / "video").glob("*.webm"), key=lambda p: p.stat().st_mtime)[-1]
    mp4 = OUT / f"{name}.mp4"
    total = L.encode_mp4(webm, mp4, phone=d.phone)
    shift = 0.3
    L.write_srt(d.steps, OUT / f"{name}.srt", shift, total)
    L.write_script_html(d.steps, OUT / f"{name}_script_hinglish.html", title, shift, total)
    L.extract_stills(mp4, d.steps, shift, W.dir / "vstills")
    print("WROTE", mp4, round(total), "s", len(d.steps), "steps")


def main():
    if "--noprep" not in sys.argv:
        prep()
    W.start_server()
    record = MODE == "record"
    d = V19(W.base_url, W.dir, record=record, video_dir=W.dir / "video" if record else None, phone=KEY == "v4")
    d.extra = {"v2": 1.8, "v4": 2.5}.get(KEY, 0.0)  # the long manual: a little more time to read each screen
    vid = None
    try:
        {"v1": video_flow, "v2": video_dm, "v3": video_operator, "v4": video_guard}[KEY](d)
    finally:
        d.sync()
        vid = d.finish()
        W.kill_server()
    (W.dir / "steps.json").write_text(json.dumps(d.steps, indent=1, ensure_ascii=False), encoding="utf-8")
    print("steps", len(d.steps), "planned hold seconds", round(sum(s["hold"] for s in d.steps)))
    if record:
        write_outputs(vid, d)


if __name__ == "__main__":
    main()
