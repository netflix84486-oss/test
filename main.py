import asyncio
import json
import os
import random
import shutil
import tempfile
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import JSONResponse
from multiprocessing import get_context

import nodriver as uc  # requires: pip install nodriver fastapi uvicorn

app = FastAPI(title="RTO Automation API", version="1.0.0")

# Ensure asyncio subprocess works on Windows (fixes NotImplementedError)
if os.name == "nt":
	try:
		asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
	except Exception:
		pass

async def human_delay(min_sec=1, max_sec=3):
	delay = random.uniform(min_sec, max_sec)
	await asyncio.sleep(delay)

async def execute_js_and_get_text(page, script: str) -> str:
	try:
		result = await page.evaluate(script)
		if hasattr(result, "value"):
			return str(result.value)
		else:
			return str(result)
	except Exception as e:
		return f"ERROR: JavaScript execution error: {e}"

async def clear_storage(page) -> None:
	# Best-effort clear of cookies/storage in the current tab
	try:
		await execute_js_and_get_text(page, """
			(function(){
				try {
					// Clear local/session storage
					if (window.localStorage) localStorage.clear();
					if (window.sessionStorage) sessionStorage.clear();
					// Best-effort cookie clear (non-HttpOnly)
					document.cookie.split(';').forEach(function(c) {
						var d = c.indexOf('=') > -1 ? c.substring(0, c.indexOf('=')) : c;
						document.cookie = d.trim() + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
					});
					return 'SUCCESS: storage cleared';
				} catch (e) { return 'ERROR: ' + e.message; }
			})()
		""")
	except:
		pass

async def run_flow(reg_no: str, chassis_no: str, rto_value: str = "53", headless: bool = True, timeout_sec: int = 120) -> Dict[str, Any]:
	messages = []
	result: Dict[str, Any] = {
		"success": False,
		"mobile_number": None,
		"details": {"messages": messages},
	}
	temp_profile = tempfile.mkdtemp(prefix="rto_profile_")
	browser = None
	page = None

	def log(msg: str):
		messages.append(msg)

	async def step_js(page, label: str, script: str) -> str:
		out = await execute_js_and_get_text(page, script)
		log(f"{label}: {out}")
		return out

	async def main():
		nonlocal browser, page
		log("Starting NoDriver automation...")
		browser_args = [
			f"--user-data-dir={temp_profile}",
			"--incognito",
			"--no-first-run",
			"--no-default-browser-check",
			"--disable-blink-features=AutomationControlled",
		]
		# Start isolated browser instance
		browser = await uc.start(headless=headless, browser_args=browser_args)
		try:
			url = "https://vahan.parivahan.gov.in/vahanservice/vahan/ui/statevalidation/homepage.xhtml?statecd=Mzc2MzM2MzAzNjY0MzIzODM3NjIzNjY0MzY2MjM3NDQ0Yw=="
			log("Loading website...")
			page = await browser.get(url)
			await human_delay(2, 4)
			await clear_storage(page)

			# Close modal if present
			try:
				close_button = await page.select(".btn-close")
				if (close_button):
					await human_delay(0.3, 1.0)
					await close_button.click()
					log("Modal dialog closed")
					await human_delay(0.8, 1.5)
				else:
					log("Modal dialog not found")
			except Exception as e:
				log(f"Modal close error: {e}")

			# RTO dropdown click (best-effort)
			await human_delay(1, 2)
			try:
				rto_label = await page.select("#fit_c_office_to_label")
				if rto_label:
					await rto_label.click()
					log("Clicked RTO dropdown")
					await human_delay(1, 2)
				else:
					log("RTO dropdown not found")
			except Exception as e:
				log(f"RTO dropdown click error: {e}")

			# Set RTO directly
			await step_js(page, "Set RTO", f"""
				(function(){{
					try {{
						var selectElement = document.getElementById('fit_c_office_to_input');
						var labelElement = document.getElementById('fit_c_office_to_label');
						if (!selectElement || !labelElement) return 'ERROR: Elements not found';
						selectElement.value = {json.dumps(rto_value)};
						labelElement.textContent = 'BURARI AUTO UNIT (DL{rto_value})';
						['input','change','blur'].forEach(function(evt){{
							selectElement.dispatchEvent(new Event(evt, {{bubbles:true}}));
						}});
						if (typeof PrimeFaces !== 'undefined' && PrimeFaces.ab) {{
							PrimeFaces.ab({{ s: "fit_c_office_to", e: "change", f: "homepageformid", p: "fit_c_office_to" }});
						}}
						return 'SUCCESS';
					}} catch(e) {{ return 'ERROR: ' + e.message; }}
				}})()
			""")
			await human_delay(2, 4)

			# Check privacy checkbox
			await step_js(page, "Click checkbox", """
				(function(){
					var selectors = ['.ui-chkbox-icon', '.ui-chkbox-box', 'input[type="checkbox"]'];
					for (var s=0;s<selectors.length;s++){
						var els = document.querySelectorAll(selectors[s]);
						for (var i=0;i<els.length;i++){
							var el = els[i];
							var r = el.getBoundingClientRect();
							if (r.width>0 && r.height>0) { el.click(); return 'SUCCESS'; }
						}
					}
					return 'ERROR: not found';
				})()
			""")
			await human_delay(0.5, 1.5)

			# First Proceed
			await step_js(page, "Proceed #1", """
				(function(){
					var btn = document.getElementById('proccedHomeButtonId');
					if (btn){ btn.click(); return 'SUCCESS: via ID'; }
					var buttons = document.querySelectorAll('button');
					for (var i=0;i<buttons.length;i++){
						var t = (buttons[i].textContent||'').toLowerCase();
						if (t.includes('proceed')) { buttons[i].click(); return 'SUCCESS: via text'; }
					}
					return 'ERROR';
				})()
			""")
			await human_delay(2, 4)

			# PrimeFaces proceed in dialog
			pf_find = await step_js(page, "Find PF proceed", """
				(function(){
					try{
						var buttons = document.querySelectorAll('button');
						for (var i=0;i<buttons.length;i++){
							var btn = buttons[i], onclick = btn.getAttribute('onclick')||'', t=(btn.textContent||'').toLowerCase();
							if (onclick.includes('PrimeFaces.ab') && t.includes('proceed')){
								return 'SUCCESS: id=' + btn.id;
							}
						}
						var b = document.getElementById('j_idt444'); if (b) return 'SUCCESS: j_idt444';
						return 'ERROR';
					}catch(e){return 'ERROR: '+e.message;}
				})()
			""")
			if "SUCCESS" in pf_find:
				await step_js(page, "Click PF proceed", """
					(function(){
						try{
							var btn = document.getElementById('j_idt444');
							if(!btn){
								var buttons = document.querySelectorAll('button');
								for (var i=0;i<buttons.length;i++){
									var b = buttons[i];
									if ((b.getAttribute('onclick')||'').includes('PrimeFaces.ab') &&
									    (b.textContent||'').toLowerCase().includes('proceed')) { btn=b; break; }
								}
							}
							if(!btn) return 'ERROR: no button';
							var oc = btn.getAttribute('onclick')||'';
							var fm = oc.match(/f:"([^"]+)"/), sm = oc.match(/s:"([^"]+)"/);
							if(fm && sm && typeof PrimeFaces!=='undefined'){ PrimeFaces.ab({s:sm[1], f:fm[1]}); return 'SUCCESS: PF.ab'; }
							btn.click(); return 'SUCCESS: click';
						}catch(e){return 'ERROR: '+e.message;}
					})()
				""")
			await human_delay(2, 3)

			# Navigate to Re-Schedule Renewal of Fitness Application
			await step_js(page, "Open Services", """
				(function(){
					var el = document.querySelector('a#navbarDropdownMenuLink');
					if(el){ el.click(); return 'SUCCESS'; }
					return 'ERROR';
				})()
			""")
			await human_delay(1, 2)
			await step_js(page, "Open RC Related Services", """
				(function(){
					try{
						var el = Array.from(document.querySelectorAll('.dropdown-item')).find(function(e){
							var t=(e.textContent||'').trim().toLowerCase();
							return t.includes('rc') && t.includes('related') && t.includes('services');
						});
						if(!el) return 'ERROR';
						el.click(); return 'SUCCESS';
					}catch(e){return 'ERROR: '+e.message;}
				})()
			""")
			await human_delay(1, 2)
			await step_js(page, "Open Re-Schedule link", """
				(function(){
					try{
						var a = document.getElementById('fitbalcTest')
						     || Array.from(document.querySelectorAll('a')).find(el => (el.textContent||'').includes('Re-Schedule Renewal of Fitness Application'));
						if(!a) return 'ERROR';
						var oc = a.getAttribute('onclick')||'';
						if(oc.includes('mojarra.jsfcljs')){
							var form = document.getElementById('loginForm');
							if(form){ mojarra.jsfcljs(form, {'fitbalcTest':'fitbalcTest','pur_cd':'86'}, ''); return 'SUCCESS: mojarra'; }
						}
						a.click(); return 'SUCCESS: click';
					}catch(e){return 'ERROR: '+e.message;}
				})()
			""")
			await human_delay(2, 3)

			# Fill validation form with provided parameters
			reg_js = json.dumps(reg_no)
			ch_js = json.dumps(chassis_no)
			fill_out = await step_js(page, "Fill form", f"""
				(function(){{
					try{{
						var reg = document.getElementById('balanceFeesFine:tf_reg_no');
						var ch = document.getElementById('balanceFeesFine:tf_chasis_no');
						if(!reg || !ch) return 'ERROR: inputs not found';
						reg.value = {reg_js};
						ch.value = {ch_js};
						['input','change','blur'].forEach(function(t){{ reg.dispatchEvent(new Event(t,{{bubbles:true}})); ch.dispatchEvent(new Event(t,{{bubbles:true}})); }});
						return 'SUCCESS';
					}}catch(e){{return 'ERROR: '+e.message;}}
				}})()
			""")
			if "SUCCESS" not in fill_out:
				return

			await human_delay(0.8, 1.5)
			val_out = await step_js(page, "Validate", """
				(function(){
					try{
						var b = document.getElementById('balanceFeesFine:validate_dtls');
						if(!b) return 'ERROR: btn';
						if (typeof PrimeFaces !== 'undefined'){
							PrimeFaces.ab({ s:'balanceFeesFine:validate_dtls', f:'balanceFeesFine', u:'balanceFeesFine:auth_panel',
								onst:function(cfg){ try{ if(PF('statusDialog')) PF('statusDialog').show(); }catch(e){} },
								onsu:function(){ try{ if(PF('statusDialog')) PF('statusDialog').hide(); }catch(e){} }
							});
							return 'SUCCESS: PF.ab';
						}
						b.click(); return 'SUCCESS: click';
					}catch(e){return 'ERROR: '+e.message;}
				})()
			""")
			await human_delay(2, 3)

			# Extract mobile number
			mob_out = await step_js(page, "Get mobile", """
				(function(){
					try{
						var m = document.getElementById('balanceFeesFine:tf_mobile');
						if(!m) return 'ERROR: field not found';
						return 'SUCCESS: ' + m.value;
					}catch(e){return 'ERROR: '+e.message;}
				})()
			""")
			if "SUCCESS:" in mob_out:
				result["mobile_number"] = mob_out.split("SUCCESS:",1)[1].strip()
				result["success"] = True

		except Exception as e:
			log(f"Error: {e}")
		finally:
			try:
				await human_delay(0.5, 1.0)
				if page:
					await clear_storage(page)
			except:
				pass
			if browser:
				try:
					browser.stop()
				except:
					pass

	try:
		await asyncio.wait_for(main(), timeout=timeout_sec)
	except asyncio.TimeoutError:
		log("ERROR: Flow timed out")

	# Cleanup temp profile
	try:
		shutil.rmtree(temp_profile, ignore_errors=True)
	except:
		pass

	return result

# Run automation in a spawned child process (Windows-friendly for subprocess)
def _child_run_flow(reg_no: str, chassis_no: str, rto_value: str, headless: bool, timeout_sec: int, out_path: str):
	try:
		# Ensure Proactor loop in child
		if os.name == "nt":
			try:
				asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
			except Exception:
				pass
		# Run the existing async flow
		res = asyncio.run(run_flow(reg_no=reg_no, chassis_no=chassis_no, rto_value=rto_value, headless=headless, timeout_sec=timeout_sec))
	except Exception as e:
		res = {"success": False, "mobile_number": None, "details": {"messages": [f"child_error: {e}"]}}
	# Persist result to temp file
	try:
		with open(out_path, "w", encoding="utf-8") as f:
			json.dump(res, f, ensure_ascii=False)
	except Exception:
		pass

def _run_in_child_sync(reg_no: str, chassis_no: str, rto_value: str, headless: bool, timeout_sec: int) -> Dict[str, Any]:
	# Create temp file path for result (avoid NamedTemporaryFile on Windows)
	fd, out_path = tempfile.mkstemp(prefix="rto_child_", suffix=".json")
	os.close(fd)
	ctx = get_context("spawn")
	p = ctx.Process(target=_child_run_flow, args=(reg_no, chassis_no, rto_value, headless, timeout_sec, out_path), daemon=True)
	p.start()
	p.join(timeout_sec + 60)  # grace period
	if p.is_alive():
		try:
			p.terminate()
		except Exception:
			pass
		p.join(10)
		result = {"success": False, "mobile_number": None, "details": {"messages": ["child_timeout"]}}
	else:
		try:
			with open(out_path, "r", encoding="utf-8") as f:
				result = json.load(f)
		except Exception as e:
			result = {"success": False, "mobile_number": None, "details": {"messages": [f"read_error: {e}"]}}
	# Cleanup
	try:
		os.remove(out_path)
	except Exception:
		pass
	return result

@app.post("/run")
async def run(
	# Accept via query as a fallback to avoid 422 when body is not JSON
	request: Request,
	reg_no: Optional[str] = None,
	chassis_no: Optional[str] = None,
	rto_value: Optional[str] = "53",
	headless: Optional[bool] = True,
	timeout_sec: Optional[int] = 180,
):
	def to_bool(v):
		if isinstance(v, bool): return v
		if v is None: return None
		if isinstance(v, (int, float)): return bool(v)
		s = str(v).strip().lower()
		return s in ("1", "true", "yes", "y", "on")
	def to_int(v, default):
		try:
			return int(v)
		except Exception:
			return default

	# If missing, try to read JSON body
	if not reg_no or not chassis_no:
		try:
			payload = await request.json()
			if isinstance(payload, dict):
				reg_no = reg_no or payload.get("reg_no")
				chassis_no = chassis_no or payload.get("chassis_no")
				rto_value = payload.get("rto_value", rto_value)
				headless = payload.get("headless", headless)
				timeout_sec = payload.get("timeout_sec", timeout_sec)
		except Exception:
			# Try form data
			try:
				form = await request.form()
				reg_no = reg_no or form.get("reg_no")
				chassis_no = chassis_no or form.get("chassis_no")
				rto_value = form.get("rto_value", rto_value)
				headless = form.get("headless", headless)
				timeout_sec = form.get("timeout_sec", timeout_sec)
			except Exception:
				pass

	if not reg_no or not chassis_no:
		raise HTTPException(status_code=400, detail="reg_no and chassis_no are required")

	headless = True if headless is None else bool(to_bool(headless))
	timeout_sec = to_int(timeout_sec, 180)
	rto_value = rto_value or "53"

	# Instead of calling run_flow directly, execute in spawned child process
	out = await asyncio.to_thread(_run_in_child_sync, reg_no, chassis_no, rto_value, headless, timeout_sec)
	return JSONResponse(content=out)

if __name__ == "__main__":
	# Run: uvicorn api:app --host 0.0.0.0 --port 8000
	import uvicorn
	uvicorn.run(app, host="0.0.0.0", port=8000)
