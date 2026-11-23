# Record real account bytes (public, read-only) for the on-chain price decoders, with DexScreener's price at the same
# moment. Usage: python -I record.py <out.json>
import base64, json, struct, sys, time, urllib.request
RPC = "https://api.mainnet-beta.solana.com"
UA = {"User-Agent": "trading-desk/1 (read-only test fixture)", "Content-Type": "application/json"}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def b58(b):
    n = int.from_bytes(b, "big"); s = ""
    while n: n, r = divmod(n, 58); s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s
def rpc(method, params):
    req = urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), UA)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["result"]
def acct(a):
    r = rpc("getAccountInfo", [a, {"encoding": "base64", "commitment": "confirmed"}])
    v = r["value"]
    return {"address": a, "owner": v["owner"], "data": v["data"][0], "slot": r["context"]["slot"]}
def get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]}), timeout=20).read())
CURVE = ("EiQ1JvEUZCXN5kQi2UdS4CdQapidqC7MCXd5sRTPpump", "8PA9idsoHRHrBVsk6bNRDEQL4HoH5JVm6RMqb8DLi54E")
POOL = ("DsuztYTs2PnXRy4NrcVRzQRK1jbvvntPGSjCbdpMpump", "B5ZX4qqS5hvcCEiyxtUbkJDuFfNL66NcPK1NzSHPcus7")
out = {"recorded_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "rpc": RPC}
t0 = time.time()
cur = acct(CURVE[1]); cmint = acct(CURVE[0])
pool = acct(POOL[1]); d = base64.b64decode(pool["data"])
bv, qv = b58(d[139:171]), b58(d[171:203])
bva, qva, pmint = acct(bv), acct(qv), acct(POOL[0])
ds_c = get("https://api.dexscreener.com/latest/dex/pairs/solana/" + CURVE[1])
ds_p = get("https://api.dexscreener.com/latest/dex/pairs/solana/" + POOL[1])
sol = get("https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112,%s,%s" % (CURVE[0], POOL[0]))
out["seconds"] = round(time.time() - t0, 2)
out["curve"] = {"mint": CURVE[0], "account": cur, "mint_account": cmint,
                "dexscreener": {k: (ds_c.get("pairs") or [{}])[0].get(k) for k in ("priceUsd", "priceNative", "dexId", "pairAddress")}}
out["pool"] = {"mint": POOL[0], "account": pool, "base_vault": bva, "quote_vault": qva, "mint_account": pmint,
               "dexscreener": {k: (ds_p.get("pairs") or [{}])[0].get(k) for k in ("priceUsd", "priceNative", "dexId", "pairAddress")}}
out["sol_usd_jupiter"] = (sol.get("So11111111111111111111111111111111111111112") or {}).get("usdPrice")
out["curve"]["jupiter_usd"] = (sol.get(CURVE[0]) or {}).get("usdPrice")
out["pool"]["jupiter_usd"] = (sol.get(POOL[0]) or {}).get("usdPrice")
json.dump(out, open(sys.argv[1], "w"), indent=1)
c = base64.b64decode(cur["data"]); vt, vq = struct.unpack_from("<QQ", c, 8)
dec_c = base64.b64decode(cmint["data"])[44]
print("curve owner", cur["owner"], "len", len(c), "disc", list(c[:8]), "vt", vt, "vq", vq, "decimals", dec_c,
      "price SOL", (vq / 1e9) / (vt / 10 ** dec_c), "ds native", out["curve"]["dexscreener"])
bamt = struct.unpack_from("<Q", base64.b64decode(bva["data"]), 64)[0]; qamt = struct.unpack_from("<Q", base64.b64decode(qva["data"]), 64)[0]
dec_p = base64.b64decode(pmint["data"])[44]
print("pool owner", pool["owner"], "len", len(d), "disc", list(d[:8]), "base_mint", b58(d[43:75]), "quote_mint", b58(d[75:107]),
      "vault owners", bva["owner"], qva["owner"], "base", bamt, "quote", qamt, "decimals", dec_p,
      "price SOL", (qamt / 1e9) / (bamt / 10 ** dec_p), "ds native", out["pool"]["dexscreener"], "sol", out["sol_usd_jupiter"], "took", out["seconds"])
