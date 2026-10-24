#!/usr/bin/env python3
"""MAT401 -- controlled download-time data collection."""
from __future__ import annotations

import argparse, csv, json, random, sys, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

import requests

SIZES = [1_000_000, 5_000_000, 10_000_000, 20_000_000]
N_ANALYSIS = 30
N_REFERENCE = 10
N_TOTAL = 40
PAUSE_SECONDS = 10.0
TIMEOUT_SECONDS = 300
MAX_ATTEMPTS_PER_TRIAL = 2
FAILURE_FLAG_THRESHOLD = 3
MAX_MAKEUP_ROUNDS = 5
SHUFFLE_SEED = 401
CHUNK = 64 * 1024
REQUEST_HEADERS = {
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "User-Agent": "MAT401-download-timing/1.0",
}
RAW_FIELDS = ["attempt_id","session_id","timestamp","file_size_bytes","round_index","block","attempt_number","observation_n","url","elapsed_seconds","bytes_received","http_status","success","error","retry_of","content_encoding"]
OBS_FIELDS = ["session_id","file_size_bytes","n","block","round_index","attempt_number","timestamp","download_time_seconds","bytes_received","throughput_mbps"]


def iso_now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def block_for_n(n): return "analysis" if n <= N_ANALYSIS else "reference"


def download_once(url, expected_bytes, trust_env=False):
    start = time.perf_counter(); status = None; encoding = ""
    try:
        with requests.Session() as s:
            s.trust_env = trust_env
            with s.get(url, headers=REQUEST_HEADERS, stream=True, allow_redirects=False, timeout=TIMEOUT_SECONDS) as r:
                status = r.status_code
                encoding = (r.headers.get("Content-Encoding") or "").strip().lower()
                if status != 200: raise RuntimeError(f"HTTP status {status}")
                if encoding not in ("", "identity"): raise RuntimeError(f"unexpected Content-Encoding: {encoding}")
                total = 0
                for chunk in r.raw.stream(CHUNK, decode_content=False): total += len(chunk)
                elapsed = time.perf_counter() - start
                if total != expected_bytes: raise RuntimeError(f"byte-count mismatch: expected {expected_bytes}, got {total}")
                return True, elapsed, total, status, encoding, ""
    except Exception as e:
        return False, time.perf_counter() - start, 0, status, encoding, str(e)


def load_real_urls(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, dict): urls = [obj[str(s)] if str(s) in obj else obj[s] for s in SIZES]
    elif isinstance(obj, list) and len(obj) == 4: urls = obj
    else: raise ValueError("urls.json must be a dict keyed by the four byte sizes or a four-item list")
    if len(set(urls)) != 4 or any(not isinstance(u,str) or not u.startswith(("http://","https://")) for u in urls): raise ValueError("Need four distinct HTTP(S) URLs")
    if any("localhost" in u or "127.0.0.1" in u for u in urls): raise ValueError("Real mode rejects localhost URLs")
    return {s:u for s,u in zip(SIZES,urls)}


def preflight(urls, trust_env=False):
    """Validate URLs without redirects and verify the actual response byte count.

    Some valid download-test endpoints (including Cloudflare's __down endpoint)
    intentionally omit Content-Length. In that case, preflight streams the full
    response and counts bytes. This is a preflight only; it is not research data.
    """
    ok = True
    print("URL PREFLIGHT")
    for s, u in urls.items():
        try:
            with requests.Session() as sess:
                sess.trust_env = trust_env
                r = sess.get(
                    u,
                    headers={**REQUEST_HEADERS, "Range": "bytes=0-0"},
                    stream=True,
                    allow_redirects=False,
                    timeout=30,
                )
                enc = (r.headers.get("Content-Encoding") or "").strip().lower()
                cr = r.headers.get("Content-Range", "")
                cl = r.headers.get("Content-Length", "")

                size = None
                if "/" in cr:
                    tail = cr.rsplit("/", 1)[1]
                    if tail.isdigit():
                        size = int(tail)
                elif cl.isdigit():
                    size = int(cl)

                # If the server provides an authoritative size, use it first.
                # If it does not (common for generated speed-test endpoints),
                # consume the response and count the actual bytes.
                counted = None
                if size is None and r.status_code == 200:
                    counted = sum(
                        len(chunk) for chunk in r.raw.stream(CHUNK, decode_content=False)
                    )
                    size = counted
                r.close()

                passed = (
                    r.status_code in (200, 206)
                    and enc in ("", "identity")
                    and size == s
                )
                extra = f"counted={counted}" if counted is not None else "counted=-"
                print(
                    f"  {s:>9} bytes: {'PASS' if passed else 'FAIL'} "
                    f"status={r.status_code} content-length={cl or '-'} "
                    f"content-range={cr or '-'} encoding={enc or 'identity'} {extra}"
                )
                ok &= passed
        except Exception as e:
            print(f"  {s:>9} bytes: FAIL {e}")
            ok = False
    return ok


class LocalTestServer:
    def __init__(self, port=0):
        rng=random.Random(401); self.payloads={s:rng.randbytes(s) for s in SIZES}; self.request_count=0
        self.fault_budget={(1_000_000,7):2,(5_000_000,9):1,(10_000_000,12):1,(20_000_000,15):1}
        outer=self
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.request_count += 1
                try:
                    from urllib.parse import urlparse, parse_qs
                    parsed=urlparse(self.path); size=int(parsed.path.rsplit("/",1)[-1]); qs=parse_qs(parsed.query)
                    outer.current_n=int(qs.get("n",[0])[0]); outer.current_attempt=int(qs.get("attempt",[0])[0])
                except: self.send_error(404); return
                if size not in outer.payloads: self.send_error(404); return
                mode=None
                # Deterministic test faults keyed to (file size, mathematical n).
                key=(size, outer.current_n)
                if outer.fault_budget.get(key,0)>0:
                    outer.fault_budget[key] -= 1
                    mode = "truncate" if key==(10_000_000,12) else "status500"
                if mode=="status500": self.send_response(500); self.end_headers(); return
                data=outer.payloads[size]
                self.send_response(200); self.send_header("Content-Length", str(len(data) if mode!="truncate" else len(data)//2)); self.send_header("Content-Type","application/octet-stream"); self.send_header("Cache-Control","no-cache"); self.end_headers()
                self.wfile.write(data if mode!="truncate" else data[:len(data)//2])
            def log_message(self,*args): pass
        self.httpd=ThreadingHTTPServer(("127.0.0.1",port),H); self.base=f"http://127.0.0.1:{self.httpd.server_address[1]}"
    def start(self):
        import threading; self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True); self.thread.start()
    def stop(self): self.httpd.shutdown(); self.httpd.server_close()
    def urls(self): return {s:f"{self.base}/file/{s}" for s in SIZES}


def run_session(session_id, urls, out_root, real=False, trust_env=False, seed=SHUFFLE_SEED):
    out=Path(out_root)/session_id; out.mkdir(parents=True,exist_ok=True)
    rawp,obsp=out/"raw_attempts.csv",out/"observations.csv"
    rawf=open(rawp,"w",newline="",encoding="utf-8"); obsf=open(obsp,"w",newline="",encoding="utf-8")
    rw,ow=csv.DictWriter(rawf,fieldnames=RAW_FIELDS,lineterminator="\n"),csv.DictWriter(obsf,fieldnames=OBS_FIELDS,lineterminator="\n"); rw.writeheader(); ow.writeheader()
    valid={s:0 for s in SIZES}; failures={s:0 for s in SIZES}; rng=random.Random(seed); round_index=0
    try:
        while any(valid[s]<N_TOTAL for s in SIZES):
            round_index += 1
            if round_index > N_TOTAL + MAX_MAKEUP_ROUNDS:
                break
            order=SIZES[:]; rng.shuffle(order)
            for s in order:
                if valid[s]>=N_TOTAL: continue
                n=valid[s]+1; block=block_for_n(n)
                success=False
                for attempt in range(1,MAX_ATTEMPTS_PER_TRIAL+1):
                    retry_of=f"{session_id}-{s}-{n}-1" if attempt>1 else ""
                    ts=iso_now(); aid=f"{session_id}-{s}-{n}-{attempt}-{round_index}"
                    success,elapsed,received,status,enc,error=download_once(urls[s] + (f"?n={n}&attempt={attempt}" if not real else ""),s,trust_env)
                    rw.writerow({"attempt_id":aid,"session_id":session_id,"timestamp":ts,"file_size_bytes":s,"round_index":round_index,"block":block,"attempt_number":attempt,"observation_n":n,"url":urls[s],"elapsed_seconds":f"{elapsed:.9f}","bytes_received":received,"http_status":status or "","success":int(success),"error":error,"retry_of":retry_of,"content_encoding":enc}); rawf.flush()
                    if success:
                        valid[s]+=1; t=elapsed; mbps=8*s/(t*1e6)
                        ow.writerow({"session_id":session_id,"file_size_bytes":s,"n":valid[s],"block":block_for_n(valid[s]),"round_index":round_index,"attempt_number":attempt,"timestamp":ts,"download_time_seconds":f"{t:.9f}","bytes_received":received,"throughput_mbps":f"{mbps:.9f}"}); obsf.flush(); break
                    failures[s]+=1
                    if attempt < MAX_ATTEMPTS_PER_TRIAL: time.sleep(0.05)
                if not success: print(f"WARN {session_id} size={s} n={n}: failed after {MAX_ATTEMPTS_PER_TRIAL} attempts")
                if all(valid[x]>=N_TOTAL for x in SIZES): break
                time.sleep(PAUSE_SECONDS if real else 0.01)
        statuses=[]
        for s in SIZES:
            state="VALID ANALYZABLE" if valid[s]==N_TOTAL and failures[s]<=FAILURE_FLAG_THRESHOLD else ("FLAGGED" if valid[s]==N_TOTAL else "INCOMPLETE")
            statuses.append((s,valid[s],failures[s],state))
        with open(out/"collection_summary.csv","w",newline="",encoding="utf-8") as f:
            w=csv.writer(f,lineterminator="\n"); w.writerow(["file_size_bytes","valid_observations","failed_http_attempts","status"]); w.writerows(statuses)
        return all(v==N_TOTAL for _,v,_,_ in statuses)
    finally: rawf.close(); obsf.close()


def main():
    p=argparse.ArgumentParser(); p.add_argument("--real",action="store_true"); p.add_argument("--session-id"); p.add_argument("--urls",default="urls.json"); p.add_argument("--out-root",default=None); p.add_argument("--check-urls",action="store_true"); p.add_argument("--test-session",default="TEST1")
    a=p.parse_args()
    if a.real:
        urls=load_real_urls(a.urls)
        passed=preflight(urls)
        if a.check_urls:
            sys.exit(0 if passed else 1)
        if not a.session_id: p.error("--real requires --session-id")
        if not passed: sys.exit("URL preflight failed; no collection started.")
        print("Preflight passed. Type START to begin real collection.")
        if input("> ").strip()!="START": sys.exit("Cancelled.")
        run_session(a.session_id,urls,"data",real=True)
    else:
        srv=LocalTestServer(); srv.start()
        try: run_session(a.test_session,srv.urls(),a.out_root or "test_data",real=False)
        finally: srv.stop()

if __name__=="__main__": main()
