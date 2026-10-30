import csv
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from playwright.sync_api import (
    sync_playwright,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)

BASE_URL = os.getenv("ORANGEHRM_URL", "https://opensource-demo.orangehrmlive.com")
DEFAULT_TIMEOUT_MS = int(os.getenv("AUTOMATION_TIMEOUT_MS", "30000"))

EMPLOYEE_COLUMNS = [
    "Id", "First (& Middle) Name", "Last Name", "Job Title",
    "Employment Status", "Sub Unit", "Supervisor",
]


class AutomationError(Exception):
    pass

@dataclass
class EmployeeInput:
    first_name: str
    last_name: str
    employee_id: str
    middle_name: str = ""


@dataclass
class AutomationResult:
    run_id: str
    success: bool = False
    message: str = ""
    employee: dict = field(default_factory=dict)
    verified_record: Optional[dict] = None
    employees: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    csv_file: Optional[str] = None
    excel_file: Optional[str] = None
    log_file: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self):
        return asdict(self)


class OrangeHRMBot:
    def __init__(
        self,
        username: str,
        password: str,
        employee: EmployeeInput,
        run_id: str,
        output_dir: str = "output",
        log_dir: str = "logs",
        headless: bool = True,
        max_pages: int = 1,
        on_step: Optional[Callable[[dict], None]] = None,
    ):
        if not username or not password:
            raise ValueError("Username and password are required.")
        self.username = username
        self.password = password
        self.employee = employee
        self.run_id = run_id
        self.headless = headless
        self.max_pages = max(1, max_pages)
        self.on_step = on_step

        self.output_dir = os.path.join(output_dir, run_id)
        self.screenshot_dir = os.path.join(self.output_dir, "screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

        self.log_file = os.path.join(log_dir, f"automation_{run_id}.log")
        self.logger = self._build_logger()
        self.result = AutomationResult(
            run_id=run_id, employee=asdict(employee), log_file=self.log_file
        )
        self.page: Optional[Page] = None
        self._step_no = 0

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"orangehrm.{self.run_id}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
        fh = logging.FileHandler(self.log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
        return logger

    def _close_logger(self):
        for h in list(self.logger.handlers):
            h.close()
            self.logger.removeHandler(h)

    def _record(self, name: str, status: str, detail: str = "", screenshot: str = None):
        step = {
            "step": self._step_no,
            "name": name,
            "status": status,
            "detail": detail,
            "screenshot": screenshot,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        if self.result.steps and self.result.steps[-1]["step"] == self._step_no:
            self.result.steps[-1] = step
        else:
            self.result.steps.append(step)
        if self.on_step:
            self.on_step(step)

    def _screenshot(self, label: str) -> Optional[str]:
        if not self.page:
            return None
        fname = f"{self._step_no:02d}_{label}.png"
        path = os.path.join(self.screenshot_dir, fname)
        try:
            self.page.screenshot(path=path, full_page=False)
            return f"{self.run_id}/screenshots/{fname}"
        except PlaywrightError as exc:
            self.logger.warning("Screenshot failed for %s: %s", label, exc)
            return None

    def _run_step(self, name: str, func, *args):
        self._step_no += 1
        label = name.lower().replace(" ", "_")
        self.logger.info("STEP %d START  - %s", self._step_no, name)
        self._record(name, "running")
        try:
            detail = func(*args) or ""
        except PlaywrightTimeoutError as exc:
            shot = self._screenshot(f"{label}_error")
            msg = f"Timed out while waiting: {str(exc).splitlines()[0]}"
            self.logger.error("STEP %d FAILED - %s | %s", self._step_no, name, msg)
            self._record(name, "failed", msg, shot)
            raise AutomationError(f"{name}: {msg}") from exc
        except Exception as exc:
            shot = self._screenshot(f"{label}_error")
            self.logger.error("STEP %d FAILED - %s | %s", self._step_no, name, exc)
            self._record(name, "failed", str(exc), shot)
            raise AutomationError(f"{name}: {exc}") from exc
        shot = self._screenshot(label)
        self.logger.info("STEP %d OK     - %s %s", self._step_no, name, f"| {detail}" if detail else "")
        self._record(name, "success", detail, shot)
        return detail

    def open_site(self):
        self.page.goto(f"{BASE_URL}/web/index.php/auth/login", wait_until="domcontentloaded")
        self.page.wait_for_selector("input[name='username']", state="visible")
        return f"Opened {BASE_URL}"

    def login(self):
        page = self.page
        page.fill("input[name='username']", self.username)
        page.fill("input[name='password']", self.password)
        page.click("button[type='submit']")

        dashboard = page.locator("h6.oxd-topbar-header-breadcrumb-module")
        invalid = page.locator(".oxd-alert-content-text")
        dashboard.or_(invalid).first.wait_for(state="visible")
        if invalid.is_visible():
            raise AutomationError(f"Login rejected: {invalid.inner_text().strip()}")
        page.wait_for_url("**/dashboard/index")
        return f"Logged in as '{self.username}'"

    def navigate_to_pim(self):
        page = self.page
        page.click("a.oxd-main-menu-item:has-text('PIM')")
        page.wait_for_url("**/pim/viewEmployeeList")
        page.wait_for_selector("h6.oxd-topbar-header-breadcrumb-module:has-text('PIM')")
        self._wait_for_table()
        return "PIM module opened"

    def add_employee(self):
        page = self.page
        emp = self.employee
        page.click("button:has-text('Add')")
        page.wait_for_url("**/pim/addEmployee")
        page.wait_for_selector("input[name='firstName']", state="visible")

        page.fill("input[name='firstName']", emp.first_name)
        if emp.middle_name:
            page.fill("input[name='middleName']", emp.middle_name)
        page.fill("input[name='lastName']", emp.last_name)

        emp_id_input = page.locator(
            "//label[normalize-space()='Employee Id']/ancestor::div[contains(@class,'oxd-input-group')]//input"
        )
        emp_id_input.click()
        emp_id_input.press("Control+A")
        emp_id_input.press("Delete")
        emp_id_input.fill(emp.employee_id)

        page.click("button[type='submit']")

        success = page.locator(".oxd-toast:has-text('Successfully Saved')")
        field_err = page.locator(".oxd-input-field-error-message")
        success.or_(field_err).first.wait_for(state="visible")
        if field_err.count() and field_err.first.is_visible():
            errors = "; ".join(t.strip() for t in field_err.all_inner_texts())
            raise AutomationError(f"Form validation failed: {errors}")

        page.wait_for_url("**/pim/viewPersonalDetails/empNumber/**")
        page.wait_for_selector("input[name='firstName']", state="visible")
        return f"Saved {emp.first_name} {emp.last_name} (ID {emp.employee_id})"

    def verify_employee(self):
        page = self.page
        emp = self.employee
        page.goto(f"{BASE_URL}/web/index.php/pim/viewEmployeeList", wait_until="domcontentloaded")
        self._wait_for_table()

        search_id = page.locator(
            "//label[normalize-space()='Employee Id']/ancestor::div[contains(@class,'oxd-input-group')]//input"
        )
        search_id.fill(emp.employee_id)
        page.click("button[type='submit']:has-text('Search')")
        self._wait_for_table()

        rows = self._read_table_rows()
        match = next((r for r in rows if r.get("Id") == emp.employee_id), None)
        if not match:
            raise AutomationError(f"Employee ID {emp.employee_id} not found in employee list")
        if emp.first_name.lower() not in match.get("First (& Middle) Name", "").lower() \
                or emp.last_name.lower() != match.get("Last Name", "").lower():
            raise AutomationError(f"Record found but names do not match: {match}")
        self.result.verified_record = match
        return f"Found in list: {match['First (& Middle) Name']} {match['Last Name']}"

    def extract_employee_list(self):
        page = self.page
        page.goto(f"{BASE_URL}/web/index.php/pim/viewEmployeeList", wait_until="domcontentloaded")
        self._wait_for_table()

        all_rows = []
        for page_no in range(1, self.max_pages + 1):
            all_rows.extend(self._read_table_rows())
            next_btn = page.locator("button.oxd-pagination-page-item--previous-next:has(i.bi-chevron-right)")
            if page_no >= self.max_pages or next_btn.count() == 0:
                break
            next_btn.first.click()
            self._wait_for_table()

        self.result.employees = all_rows
        self._export(all_rows)
        return f"Extracted {len(all_rows)} employee rows from {min(page_no, self.max_pages)} page(s)"

    def logout(self):
        page = self.page
        page.click("span.oxd-userdropdown-tab")
        page.click("a.oxd-userdropdown-link:has-text('Logout')")
        page.wait_for_url("**/auth/login")
        page.wait_for_selector("input[name='username']", state="visible")
        return "Logged out, back on login page"

    def _wait_for_table(self):
        page = self.page
        page.wait_for_selector(".oxd-table-body", state="attached")
        page.locator(".oxd-loading-spinner").wait_for(state="hidden")
        page.wait_for_selector(
            ".oxd-table-card, span.oxd-text:has-text('No Records Found')", state="attached"
        )

    def _read_table_rows(self) -> list:
        rows = []
        for card in self.page.locator(".oxd-table-card").all():
            cells = card.locator(".oxd-table-cell").all_inner_texts()
            values = [c.strip() for c in cells[1:1 + len(EMPLOYEE_COLUMNS)]]
            if values:
                rows.append(dict(zip(EMPLOYEE_COLUMNS, values)))
        return rows

    def _export(self, rows: list):
        csv_path = os.path.join(self.output_dir, "employee_list.csv")
        xlsx_path = os.path.join(self.output_dir, "employee_list.xlsx")

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=EMPLOYEE_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        wb = Workbook()
        ws = wb.active
        ws.title = "Employees"
        ws.append(EMPLOYEE_COLUMNS)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="F28C38")
        for r in rows:
            ws.append([r.get(c, "") for c in EMPLOYEE_COLUMNS])
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col) + 2
            ws.column_dimensions[col[0].column_letter].width = min(width, 40)
        wb.save(xlsx_path)

        self.result.csv_file = f"{self.run_id}/employee_list.csv"
        self.result.excel_file = f"{self.run_id}/employee_list.xlsx"
        self.logger.info("Exported data -> %s, %s", csv_path, xlsx_path)

    def run(self) -> AutomationResult:
        self.result.started_at = datetime.now().isoformat(timespec="seconds")
        self.logger.info("=" * 70)
        self.logger.info("Run %s | employee=%s", self.run_id, asdict(self.employee))
        logged_in = False
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                context = browser.new_context(viewport={"width": 1366, "height": 768})
                context.set_default_timeout(DEFAULT_TIMEOUT_MS)
                self.page = context.new_page()
                try:
                    self._run_step("Open Website", self.open_site)
                    self._run_step("Login", self.login)
                    logged_in = True
                    self._run_step("Navigate to PIM", self.navigate_to_pim)
                    self._run_step("Add Employee", self.add_employee)
                    self._run_step("Verify Employee", self.verify_employee)
                    self._run_step("Extract Employee List", self.extract_employee_list)
                    self._run_step("Logout", self.logout)
                    logged_in = False
                    self.result.success = True
                    self.result.message = "Automation completed successfully."
                except AutomationError as exc:
                    self.result.message = str(exc)
                    if logged_in:
                        try:
                            self._run_step("Logout (cleanup)", self.logout)
                        except AutomationError:
                            self.logger.warning("Cleanup logout failed")
                finally:
                    context.close()
                    browser.close()
        except PlaywrightError as exc:
            self.result.message = f"Browser error: {exc}"
            self.logger.exception("Browser-level failure")
        except Exception as exc:
            self.result.message = f"Unexpected error: {exc}"
            self.logger.exception("Unexpected failure")
        finally:
            self.result.finished_at = datetime.now().isoformat(timespec="seconds")
            self.logger.info("Run finished | success=%s | %s", self.result.success, self.result.message)
            self._close_logger()
        return self.result
