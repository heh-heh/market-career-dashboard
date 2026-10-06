#!/usr/bin/env python3
"""Guarded Toss US auto trader with regular and extended-hours support. Live mode is OFF by default."""
from __future__ import annotations
import base64, json, math, os, secrets, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from toss_rate_limit import wait_for_slot, group_for_path

KST = timezone(timedelta(hours=9))

class LiveAutoTrader:
    def __init__(self, root):
        self.root = Path(root)
        self.state_path = self.root / "live_trading_state.json"
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.engine_enabled = False
        self.live_armed = False
        self.auto_enabled = False
        self.live_halted = False
        self.last_error = None
        self.last_action = None
        self.last_scan_at = None
        self.session = "UNKNOWN"
        self.session_message = ""
        self.candidate = None
        self.rankings = []
        self.managed_symbol = None
        self.entry_price = None
        self.pending_order_id = None
        self.pending_side = None
        self.reference_mid = None
        self.target_pct = None
        self.entry_time = None
        self._token = {"value": "", "expires_at": 0.0}
        self._token_provider = None
        self._load_state()

    @property
    def live_enabled(self):
        return os.getenv("TRADING_MODE", "paper").lower() == "live" and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    @property
    def arm_phrase(self):
        return os.getenv("LIVE_ARM_PHRASE", "")

    @property
    def max_order_usd(self):
        return max(10.0, float(os.getenv("MAX_ORDER_USD", "100")))

    @property
    def max_loss_usd(self):
        return max(1.0, float(os.getenv("MAX_DAILY_LOSS_USD", "50")))

    @property
    def max_loss_krw(self):
        return max(1000.0, float(os.getenv("MAX_DAILY_LOSS_KRW", "50000")))

    @property
    def scan_interval(self):
        return max(30, int(os.getenv("AUTO_SCAN_INTERVAL_SEC", "60")))

    @property
    def min_us_price(self):
        return max(1.0, float(os.getenv("AUTO_US_MIN_PRICE", "5")))

    @property
    def max_us_price(self):
        return max(self.min_us_price, float(os.getenv("AUTO_US_MAX_PRICE", "100")))

    @property
    def min_us_volume(self):
        return max(1.0, float(os.getenv("AUTO_US_MIN_TRADING_VOLUME", "1000000")))

    def _day(self):
        return datetime.now(KST).date().isoformat()

    def _load_state(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
            self.managed_symbol = raw.get("managed_symbol")
            self.entry_price = raw.get("entry_price")
            self.pending_order_id = raw.get("pending_order_id")
            self.pending_side = raw.get("pending_side")
            self.reference_mid = raw.get("reference_mid")
            self.target_pct = raw.get("target_pct")
            self.entry_time = raw.get("entry_time")
            self.live_halted = bool(raw.get("live_halted", False)) if raw.get("day") == self._day() else False
        except Exception:
            pass
        self.engine_enabled = False
        self.live_armed = False
        self.auto_enabled = False

    def _ensure_day(self):
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if raw.get("day") != self._day():
                self.live_halted = False
                self._save_state()
        except Exception:
            pass

    def _save_state(self):
        payload = {
            "day": self._day(),
            "managed_symbol": self.managed_symbol,
            "entry_price": self.entry_price,
            "pending_order_id": self.pending_order_id,
            "pending_side": self.pending_side,
            "reference_mid": self.reference_mid,
            "target_pct": self.target_pct,
            "entry_time": self.entry_time,
            "live_halted": self.live_halted,
        }
        tmp = self.state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
            os.chmod(self.state_path, 0o600)
        except Exception:
            pass

    def _load_secrets(self):
        p = self.root / "server_secrets.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            return {}

    def set_token_provider(self, provider):
        self._token_provider = provider

    def _token_get(self):
        if self._token_provider is not None:
            token = str(self._token_provider() or "")
            if not token:
                raise RuntimeError("Toss access token을 발급받지 못했습니다.")
            return token
        now = time.time()
        if self._token["value"] and now < self._token["expires_at"] - 60:
            return self._token["value"]
        cfg = self._load_secrets().get("toss", {})
        cid = str(cfg.get("app_key") or "").strip()
        secret = str(cfg.get("app_secret") or "").strip()
        if not cid or not secret:
            raise RuntimeError("Toss App Key/Secret이 서버에 설정되지 않았습니다.")
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        basic = base64.b64encode((cid + ":" + secret).encode()).decode()
        req = urllib.request.Request(
            "https://openapi.tossinvest.com/oauth2/token",
            data=body,
            headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            obj = json.loads(r.read())
        tok = str(obj.get("access_token") or "")
        if not tok:
            raise RuntimeError("Toss OAuth 응답에 access_token이 없습니다.")
        self._token = {"value": tok, "expires_at": now + max(120, int(obj.get("expires_in") or 86400))}
        return tok

    def _account_seq(self):
        envv = str(os.getenv("TOSS_ACCOUNT_SEQ", "")).strip()
        if envv:
            return envv
        cfg = self._load_secrets().get("toss", {})
        saved = str(cfg.get("account_seq") or "").strip()
        if saved:
            return saved
        accounts = self._api("GET", "/api/v1/accounts", account=False)
        accounts = accounts if isinstance(accounts, list) else []
        valid = [x for x in accounts if x.get("accountSeq") is not None]
        if len(valid) == 1:
            return str(valid[0]["accountSeq"])
        if not valid:
            raise RuntimeError("정상 토스증권 계좌를 찾지 못했습니다.")
        raise RuntimeError("계좌가 여러 개입니다. TOSS_ACCOUNT_SEQ를 설정하세요.")

    def _api(self, method, path, params=None, body=None, account=True):
        token = self._token_get()
        query = urllib.parse.urlencode(params or {})
        url = "https://openapi.tossinvest.com" + path + (("?" + query) if query else "")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "market-career-dashboard-live/1.0"}
        if account:
            headers["X-Tossinvest-Account"] = self._account_seq()
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            wait_for_slot(group_for_path(path))
            with urllib.request.urlopen(req, timeout=8) as r:
                obj = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            if e.code == 429:
                retry_after = 0.0
                try:
                    retry_after = float(e.headers.get("Retry-After") or 0)
                except Exception:
                    pass
                time.sleep(max(1.0, min(30.0, retry_after or 2.0)))
                wait_for_slot(group_for_path(path))
                try:
                    with urllib.request.urlopen(req, timeout=8) as r:
                        obj = json.loads(r.read())
                    return obj.get("result", obj) if isinstance(obj, dict) else obj
                except urllib.error.HTTPError as e_retry:
                    detail_retry = e_retry.read().decode("utf-8", "ignore")
                    raise RuntimeError("Toss API rate-limit retry failed: " + detail_retry[:500])
            if e.code == 401 and self._token_provider is not None and any(x in detail for x in ("invalid-token","expired-token","token-revoked")):
                token = str(self._token_provider(True) or "")
                if not token:
                    raise RuntimeError("Toss access token 재발급에 실패했습니다.")
                headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=8) as r:
                        obj = json.loads(r.read())
                except urllib.error.HTTPError as e2:
                    detail2 = e2.read().decode("utf-8", "ignore")
                    raise RuntimeError("Toss API 실패: " + detail2[:500])
            else:
                raise RuntimeError("Toss API 실패: " + detail[:500])
        return obj.get("result", obj) if isinstance(obj, dict) else obj

    def _calendar(self):
        # Toss API's `date` parameter is the US-local calendar date, not KST.
        us_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        obj = self._api("GET", "/api/v1/market-calendar/US", {"date": us_date}, account=False)
        today_obj = obj.get("today") if isinstance(obj, dict) else None
        if not today_obj:
            return "UNKNOWN", "미국 장 운영 정보 없음"

        now = datetime.now(KST)
        sessions = [
            ("DAY", today_obj.get("dayMarket"), "데이마켓 자동주문 가능"),
            ("PRE", today_obj.get("preMarket"), "프리마켓 자동주문 가능"),
            ("REGULAR", today_obj.get("regularMarket"), "정규장 자동주문 가능"),
            ("AFTER", today_obj.get("afterMarket"), "애프터마켓 자동주문 가능"),
        ]
        for name, window, message in sessions:
            if not window:
                continue
            try:
                start = datetime.fromisoformat(window["startTime"]).astimezone(KST)
                end = datetime.fromisoformat(window["endTime"]).astimezone(KST)
                if start <= now <= end:
                    return name, message
            except Exception:
                continue

        return "CLOSED", "현재 미국 거래 세션이 아닙니다."

    def _rankings(self):
        return self._api(
            "GET", "/api/v1/rankings",
            {"type": "MARKET_TRADING_VOLUME", "marketCountry": "US", "duration": "realtime",
             "excludeInvestmentCaution": "true", "count": "100"},
            account=False,
        )

    def _stocks(self, symbols):
        if not symbols:
            return {}
        rows = self._api("GET", "/api/v1/stocks", {"symbols": ",".join(symbols[:200])}, account=False)
        return {str(x.get("symbol")): x for x in rows} if isinstance(rows, list) else {}

    def _candles(self, symbol):
        obj = self._api("GET", "/api/v1/candles", {"symbol": symbol, "interval": "1m", "count": "240"}, account=False)
        rows = obj.get("candles", []) if isinstance(obj, dict) else []
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _analyze(rows):
        """3-minute adaptive V2 signal engine.
        Rows are 1-minute Toss candles, newest-first.
        """
        rows = list(reversed(rows))
        bars = []
        for i in range(0, len(rows) - 2, 3):
            chunk = rows[i:i+3]
            if len(chunk) < 3:
                break
            try:
                o=float(chunk[0]["openPrice"]); h=max(float(x["highPrice"]) for x in chunk)
                l=min(float(x["lowPrice"]) for x in chunk); c=float(chunk[-1]["closePrice"])
                v=sum(float(x.get("volume") or 0) for x in chunk)
                bars.append({"o":o,"h":h,"l":l,"c":c,"v":v})
            except (KeyError,TypeError,ValueError):
                continue
        if len(bars) < 25:
            return {"signal":"HOLD","reason":"3분봉 데이터 부족","price":bars[-1]["c"] if bars else None,"patternScore":0}
        eps=1e-9
        closes=[b["c"] for b in bars]; vols=[b["v"] for b in bars]
        def sma(a,n): return sum(a[-n:])/n
        ma5=sma(closes,5); ma10=sma(closes,10); ma20=sma(closes,20)
        ma20_3=sma(closes[:-3],20)
        trs=[]
        for i,b in enumerate(bars):
            prev=closes[i-1] if i else b["c"]
            trs.append(max(b["h"]-b["l"],abs(b["h"]-prev),abs(b["l"]-prev)))
        atr=sum(trs[-14:])/14.0
        med20=sorted(vols[-20:])[9]
        vma5=sma(vols,5); rvol=vols[-1]/max(med20,eps)
        body=abs(bars[-1]["c"]-bars[-1]["o"]); rng=max(bars[-1]["h"]-bars[-1]["l"],eps)
        body_ratio=body/rng
        prev=bars[-2]; prev2=bars[-3]
        # RSI14
        gains=[]; losses=[]
        for i in range(max(1,len(closes)-14),len(closes)):
            d=closes[i]-closes[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
        ag=sum(gains)/max(1,len(gains)); al=sum(losses)/max(1,len(losses))
        rsi=100.0 if al==0 and ag>0 else 50.0 if al==0 else 100.0-100.0/(1.0+ag/al)
        day_high=max(b["h"] for b in bars[-130:]); day_low=min(b["l"] for b in bars[-130:])
        reference_mid=None; risk_r=None; score=0; patterns=[]
        # P1 upper-line breakout
        p1=(prev["c"]<prev["o"] and prev["c"]>=sma(closes[:-1],5)*0.998
            and prev["v"]<=sma(vols[:-1],5)*0.90 and bars[-1]["c"]>bars[-1]["o"]
            and bars[-1]["c"]>prev["h"] and rvol>=1.40 and vols[-1]>=vma5*1.35
            and body_ratio>=0.45 and ma5>ma10 and bars[-1]["c"]<=ma5+atr)
        if p1: score+=5; patterns.append("P1")
        # P2 5MA turnaround
        ma5p=sma(closes[:-1],5); ma10p=sma(closes[:-1],10)
        p2=(prev["c"]<ma5p and prev["c"]>ma10p and prev["l"]>=ma10p*0.997
            and bars[-1]["c"]>bars[-1]["o"] and bars[-1]["c"]>ma5
            and bars[-1]["c"]>prev["h"]*0.999 and vols[-1]>=max(prev["v"]*1.8,vma5*1.2)
            and body_ratio>=0.45 and (bars[-1]["c"]-bars[-1]["l"])/rng>=0.55)
        if p2: score+=4; patterns.append("P2")
        # P3 20MA pullback
        ma20p=sma(closes[:-1],20)
        pullback=abs(prev["l"]/ma20p-1)<=0.004 and min(prev["o"],prev["c"])>=ma20p*0.997
        prior_high=max(closes[-6:-2])
        p3=(ma20>ma20_3 and pullback and prev["c"]/max(prior_high,eps)-1<=-0.008
            and bars[-1]["c"]>bars[-1]["o"] and bars[-1]["c"]>ma5
            and bars[-1]["c"]>prev["h"] and vols[-1]>=vma5*1.25 and rvol>=1.20)
        if p3: score+=3; patterns.append("P3")
        # P4 opening-range/pivot retest, only when enough same-session bars are available
        p4=False
        if len(bars)>=15:
            orh=max(b["h"] for b in bars[:6])
            broke=any(b["c"]>orh*1.002 for b in bars[6:-2])
            retest=min(prev["l"],prev2["l"])>=orh*0.997 and min(prev["c"],prev2["c"])>=orh*0.999
            p4=broke and retest and bars[-1]["c"]>bars[-1]["o"] and bars[-1]["c"]>prev["h"] and vols[-1]>=prev["v"]*1.4 and rvol>=1.20
            if p4: score+=3; patterns.append("P4")
        # P5 compression breakout
        if len(bars)>=12:
            spreads=[]
            for j in range(-10,0):
                a5=sma(closes[:j],5); a10=sma(closes[:j],10); a20=sma(closes[:j],20)
                spreads.append((max(a5,a10,a20)-min(a5,a10,a20))/max(min(a5,a10,a20),eps))
            box_high=max(b["h"] for b in bars[-11:-1]); box_range=box_high-min(b["l"] for b in bars[-11:-1])
            p5=(max(spreads)<=0.006 and box_range<=atr*2.0 and bars[-1]["c"]>box_high*1.001
                and vols[-1]>=max(vols[-11:-1])*1.60 and vols[-1]>=vma5*1.35 and body_ratio>=0.55)
            if p5: score+=4; patterns.append("P5")
        if rvol>=1.50 and vols[-1]>prev["v"] and vols[-1]>vma5: score+=2; patterns.append("VOL")
        if ma5>ma10>ma20 and ma20>ma20_3: score+=1; patterns.append("TREND")
        # Common filters from V2 using available session window.
        prev_close=closes[-2]
        day_high_pct=(day_high/max(prev_close,eps)-1)*100
        day_low_recovery=(closes[-1]/max(day_low,eps)-1)*100
        dollar_value=closes[-1]*vols[-1]
        gap_atr=abs(closes[-1]-ma5)/max(atr,eps)
        common=(5<=closes[-1]<=300 and rvol>=1.20 and dollar_value>=1500000
                and (day_high_pct>=3.0 or day_low_recovery>=4.0) and gap_atr<=1.20)
        # Reference candle: latest strong volume bullish candle before/at signal.
        if score>=7:
            candidates=[b for b in bars[max(0,len(bars)-10):] if b["c"]>b["o"] and b["v"]>=vma5*1.25]
            if candidates:
                ref=candidates[-1]; reference_mid=(ref["o"]+ref["c"])/2.0
                risk_r=closes[-1]-reference_mid
        signal="BUY" if common and score>=7 and risk_r is not None and risk_r>0 and risk_r<=atr*1.20 else "HOLD"
        if signal=="BUY":
            target=max(0.8,min(2.5,1.5*(risk_r/max(closes[-1],eps))*100))
            reason="V2 BUY "+("+".join(patterns))+f" score={score}"
        else:
            target=None; reason=f"V2 대기 score={score} common={'OK' if common else 'NO'}"
        return {"signal":signal,"reason":reason,"price":closes[-1],
                "ma5":round(ma5,4),"ma10":round(ma10,4),"ma20":round(ma20,4),
                "rsi14":round(rsi,2),"rvol":round(rvol,2),"atr14":round(atr,4),
                "patternScore":score,"patterns":patterns,"referenceMid":reference_mid,
                "riskR":risk_r,"targetPct":target,"volume3m":vols[-1],
                "dayHighPct":round(day_high_pct,3),"dayLowRecoveryPct":round(day_low_recovery,3),
                "dollarValue3m":round(dollar_value,2),"ma5GapAtr":round(gap_atr,3)}

    def _scan_candidates(self):
        ranked = self._rankings()
        ranked = ranked if isinstance(ranked, dict) else {}
        raw = ranked.get("rankings", [])
        symbols = [str(x.get("symbol")).upper() for x in raw[:50] if x.get("symbol")]
        meta = self._stocks(symbols)
        out = []
        for item in raw[:50]:
            sym = str(item.get("symbol") or "").upper()
            info = meta.get(sym, {})
            p = item.get("price") or {}
            try:
                price = float(p.get("lastPrice"))
                volume = float(item.get("tradingVolume") or 0)
                amount = float(item.get("tradingAmount") or 0)
                change = float(p.get("changeRate") or 0) * 100
            except Exception:
                continue
            if info.get("securityType") != "STOCK" or info.get("isCommonShare") is not True or info.get("status") != "ACTIVE":
                continue
            if str(info.get("currency") or "").upper() != "USD" or not (self.min_us_price <= price <= self.max_us_price):
                continue
            if volume < self.min_us_volume:
                continue
            a = self._analyze(self._candles(sym))
            out.append({"rank": item.get("rank"), "symbol": sym,
                        "name": info.get("name") or info.get("englishName") or sym,
                        "tradingVolume": volume,
                        "tradingAmountUsd": round(amount, 2),
                        "price": price, "changePct": round(change, 3), **a})
            if len(out) >= 5:
                break
        out.sort(key=lambda x: x["tradingVolume"], reverse=True)
        return out

    def _holdings(self, symbol=None):
        params = {"symbol": symbol} if symbol else None
        return self._api("GET", "/api/v1/holdings", params, account=True)

    def _daily_loss(self):
        h = self._holdings()
        d = (h.get("dailyProfitLoss") or {}).get("amount") or {}
        return float(d.get("usd") or 0), float(d.get("krw") or 0)

    def _buying_power(self):
        x = self._api("GET", "/api/v1/buying-power", {"currency": "USD"}, account=True)
        return float(x.get("cashBuyingPower") or 0)

    def _sellable(self, symbol):
        x = self._api("GET", "/api/v1/sellable-quantity", {"symbol": symbol}, account=True)
        return float(x.get("sellableQuantity") or 0)

    def _open_orders(self):
        x = self._api("GET", "/api/v1/orders", {"status": "OPEN"}, account=True)
        return x.get("orders", []) if isinstance(x, dict) else []

    def _order_detail(self, oid):
        return self._api("GET", "/api/v1/orders/" + urllib.parse.quote(str(oid), safe=""), account=True)

    def _refresh_pending(self):
        if not self.pending_order_id:
            return
        detail = self._order_detail(self.pending_order_id)
        status = str(detail.get("status") or "").upper()
        ex = detail.get("execution") or {}
        if self.pending_side == "BUY" and ex.get("averageFilledPrice"):
            self.entry_price = float(ex["averageFilledPrice"])
        if status == "FILLED":
            if self.pending_side == "SELL":
                self.managed_symbol, self.entry_price = None, None
                self.reference_mid, self.target_pct, self.entry_time = None, None, None
                self.last_action = "SELL_FILLED"
            else:
                self.last_action = "BUY_FILLED " + str(self.managed_symbol)
            self.pending_order_id, self.pending_side = None, None
            self._save_state()
        elif status in {"CANCELED", "REJECTED", "REPLACED"}:
            if self.pending_side == "BUY":
                self.managed_symbol, self.entry_price = None, None
                self.reference_mid, self.target_pct, self.entry_time = None, None, None
            self.last_error = "주문 종료: " + status
            self.pending_order_id, self.pending_side = None, None
            self._save_state()

    def _submit_buy(self, c):
        usd_loss, krw_loss = self._daily_loss()
        if usd_loss <= -self.max_loss_usd or krw_loss <= -self.max_loss_krw:
            self.live_halted = True
            self.last_error = "일일 손실 제한 초과"
            self._save_state()
            return
        for o in self._open_orders():
            if str(o.get("symbol") or "").upper() == c["symbol"]:
                return

        bp = self._buying_power()
        amount = min(self.max_order_usd, bp * 0.95)
        price = float(c.get("price") or 0)
        if amount < 10:
            self.last_error = "USD 매수 가능금액 부족"
            return

        # Toss only accepts US amount-based/fractional orders during regular hours.
        # Outside regular hours use a whole-share market order so the bot can trade
        # during DAY/PRE/AFTER sessions without sending a known-invalid orderAmount.
        if self.session == "REGULAR":
            body = {
                "clientOrderId": "mktdash-" + secrets.token_hex(10),
                "symbol": c["symbol"], "side": "BUY", "orderType": "MARKET",
                "orderAmount": "%.2f" % amount,
            }
            action_text = "BUY_SUBMITTED %s $%.2f" % (c["symbol"], amount)
        else:
            if price <= 0:
                self.last_error = "매수 가격을 확인할 수 없습니다."
                return
            quantity = math.floor(amount / price)
            if quantity < 1:
                self.last_error = "시간외 매수 불가: 최대 주문금액으로 1주도 살 수 없습니다."
                return
            body = {
                "clientOrderId": "mktdash-" + secrets.token_hex(10),
                "symbol": c["symbol"], "side": "BUY", "orderType": "MARKET",
                "quantity": str(quantity),
            }
            action_text = "BUY_SUBMITTED %s %d주 · %s" % (c["symbol"], quantity, self.session)

        result = self._api("POST", "/api/v1/orders", body=body, account=True)
        oid = result.get("orderId")
        if not oid:
            raise RuntimeError("매수 주문 ID가 없습니다.")
        self.managed_symbol, self.entry_price = c["symbol"], None
        self.reference_mid = c.get("referenceMid")
        self.target_pct = c.get("targetPct")
        self.entry_time = time.time()
        self.pending_order_id, self.pending_side = oid, "BUY"
        self.last_action = action_text
        self._save_state()

    def _submit_sell(self, price, reason):
        if not self.managed_symbol or self.pending_order_id:
            return
        qty = self._sellable(self.managed_symbol)
        if qty <= 0:
            self.last_error = "매도 가능 수량이 없습니다."
            return

        # Outside regular hours Toss only permits whole-share quantity for a
        # market sell. Do not partially unwind a fractional bot position.
        if self.session != "REGULAR":
            whole_qty = math.floor(qty)
            if whole_qty < 1 or abs(qty - whole_qty) > 1e-9:
                self.last_error = "시간외 매도 대기: 소수점 보유분은 정규장에서만 전량 매도합니다."
                return
            qty_text = str(whole_qty)
            session_text = " · " + self.session
        else:
            qty_text = ("%.6f" % qty).rstrip("0").rstrip(".")
            session_text = ""

        result = self._api("POST", "/api/v1/orders", body={
            "clientOrderId": "mktdash-" + secrets.token_hex(10),
            "symbol": self.managed_symbol, "side": "SELL", "orderType": "MARKET",
            "quantity": qty_text,
        }, account=True)
        oid = result.get("orderId")
        if not oid:
            raise RuntimeError("매도 주문 ID가 없습니다.")
        self.pending_order_id, self.pending_side = oid, "SELL"
        self.last_action = "SELL_SUBMITTED %s reason=%s price=%.2f%s" % (self.managed_symbol, reason, price, session_text)
        self._save_state()

    def account_snapshot(self):
        """Return live wallet, holdings and recent order history for the dashboard."""
        with self.lock:
            holdings = self._holdings()
            try:
                krw_power = self._api("GET", "/api/v1/buying-power", {"currency": "KRW"}, account=True)
            except Exception:
                krw_power = {}
            try:
                usd_power = self._api("GET", "/api/v1/buying-power", {"currency": "USD"}, account=True)
            except Exception:
                usd_power = {}
            recent_orders = []
            open_orders = []
            try:
                closed = self._api("GET", "/api/v1/orders", {"status": "CLOSED", "limit": "50"}, account=True)
                if isinstance(closed, dict):
                    recent_orders = closed.get("orders", []) or []
            except Exception as exc:
                self.last_error = "주문내역 조회 실패: " + str(exc)
            try:
                opened = self._api("GET", "/api/v1/orders", {"status": "OPEN"}, account=True)
                if isinstance(opened, dict):
                    open_orders = opened.get("orders", []) or []
            except Exception:
                pass

            def num(path, default=0.0):
                cur = holdings
                for key in path:
                    cur = cur.get(key) if isinstance(cur, dict) else None
                try:
                    return float(cur or default)
                except (TypeError, ValueError):
                    return float(default)

            return {
                "wallet": {
                    "krwBuyingPower": float(krw_power.get("cashBuyingPower") or 0),
                    "usdBuyingPower": float(usd_power.get("cashBuyingPower") or 0),
                    "totalPurchaseKrw": num(("totalPurchaseAmount", "krw")),
                    "totalPurchaseUsd": num(("totalPurchaseAmount", "usd")),
                    "marketValueKrw": num(("marketValue", "amount", "krw")),
                    "marketValueUsd": num(("marketValue", "amount", "usd")),
                    "profitLossKrw": num(("profitLoss", "amount", "krw")),
                    "profitLossUsd": num(("profitLoss", "amount", "usd")),
                    "profitLossRate": num(("profitLoss", "rate")) * 100,
                    "dailyProfitLossKrw": num(("dailyProfitLoss", "amount", "krw")),
                    "dailyProfitLossUsd": num(("dailyProfitLoss", "amount", "usd")),
                },
                "holdings": holdings.get("items", []) if isinstance(holdings, dict) else [],
                "openOrders": open_orders,
                "recentOrders": recent_orders,
            }

    def scan(self, allow_orders=False):
        with self.lock:
            self._ensure_day()
            self.last_scan_at = datetime.now(KST).isoformat(timespec="seconds")
            self.last_error = None
            if not self.live_enabled:
                self.session, self.session_message = "LOCKED", "TRADING_MODE=live + LIVE_TRADING_ENABLED=true 필요"
                return self.status()
            try:
                if self.pending_order_id:
                    self._refresh_pending()
                self.session, self.session_message = self._calendar()
                candidates = self._scan_candidates()
                self.rankings = candidates[:5]
                self.candidate = candidates[0] if candidates else None

                if self.managed_symbol:
                    managed = next((x for x in candidates if x["symbol"] == self.managed_symbol), None)
                    if managed is None:
                        a = self._analyze(self._candles(self.managed_symbol))
                        managed = {"symbol": self.managed_symbol, "name": self.managed_symbol, "tradingAmountUsd": 0, **a}
                    pnl = 0.0
                    if self.entry_price and managed.get("price"):
                        pnl = (float(managed["price"]) / float(self.entry_price) - 1) * 100
                    managed["entryPrice"] = self.entry_price
                    managed["pnlPct"] = round(pnl, 3)
                    self.candidate = managed
                    if allow_orders and self.session in {"DAY", "PRE", "REGULAR", "AFTER"} and not self.pending_order_id:
                        reason = None
                        price_now = float(managed["price"])
                        if self.reference_mid is not None and price_now < float(self.reference_mid):
                            reason = "v2_reference_mid_stop"
                        elif self.entry_price and self.target_pct is not None and price_now >= float(self.entry_price) * (1 + float(self.target_pct)/100):
                            reason = "v2_target"
                        elif self.entry_time and time.time()-float(self.entry_time) >= 1800 and price_now < float(self.entry_price)*1.003 and price_now < float(managed.get("ma5") or price_now):
                            reason = "v2_time_stop"
                        elif managed.get("signal") == "SELL":
                            reason = "v2_trend_exit"
                        if reason:
                            self._submit_sell(price_now, reason)
                    self._save_state()
                    return self.status()

                if allow_orders and self.session in {"DAY", "PRE", "REGULAR", "AFTER"} and not self.live_halted and not self.pending_order_id:
                    buy = next((x for x in candidates if x.get("signal") == "BUY"), None)
                    if buy:
                        self._submit_buy(buy)
                self._save_state()
            except Exception as exc:
                self.last_error = str(exc)
            return self.status()

    def set_engine(self, enabled):
        with self.lock:
            self.engine_enabled = bool(enabled)
            if not self.engine_enabled:
                self.auto_enabled = False
            return self.status()

    def arm(self, phrase):
        with self.lock:
            if not self.live_enabled:
                raise RuntimeError("실거래 환경이 잠겨 있습니다.")
            if not self.arm_phrase or not secrets.compare_digest(str(phrase or ""), self.arm_phrase):
                raise RuntimeError("LIVE_ARM_PHRASE가 일치하지 않습니다.")
            if self.live_halted:
                raise RuntimeError("일일 손실 제한으로 정지되었습니다.")
            self.live_armed = True
            return self.status()

    def disarm(self):
        with self.lock:
            self.live_armed = False
            self.auto_enabled = False
            return self.status()

    def set_auto(self, enabled):
        with self.lock:
            if enabled and not self.live_armed:
                raise RuntimeError("먼저 실거래 ARM을 하세요.")
            if enabled and not self.engine_enabled:
                raise RuntimeError("먼저 실거래 엔진을 시작하세요.")
            if enabled and self.live_halted:
                raise RuntimeError("일일 손실 제한으로 정지되었습니다.")
            self.auto_enabled = bool(enabled)
            return self.status()

    def run_loop(self):
        while not self.stop_event.is_set():
            try:
                if self.live_enabled:
                    self.scan(self.engine_enabled and self.live_armed and self.auto_enabled)
            except Exception as exc:
                self.last_error = str(exc)
            self.stop_event.wait(self.scan_interval)

    def start(self):
        threading.Thread(target=self.run_loop, daemon=True, name="live-auto-trader").start()

    def status(self):
        with self.lock:
            return {
                "liveTradingEnabled": self.live_enabled,
                "engineEnabled": self.engine_enabled,
                "liveArmed": self.live_armed,
                "liveAutoEnabled": self.auto_enabled,
                "liveHalted": self.live_halted,
                "session": self.session,
                "sessionMessage": self.session_message,
                "candidate": self.candidate,
                "rankings": self.rankings,
                "managedSymbol": self.managed_symbol,
                "entryPrice": self.entry_price,
                "pendingOrderId": self.pending_order_id,
                "pendingSide": self.pending_side,
                "lastAction": self.last_action,
                "lastError": self.last_error,
                "lastScanAt": self.last_scan_at,
                "limits": {
                    "maxOrderUsd": self.max_order_usd,
                    "maxDailyLossUsd": self.max_loss_usd,
                    "maxDailyLossKrw": self.max_loss_krw,
                    "takeProfitPct": float(os.getenv("AUTO_TAKE_PROFIT_PCT", "1.5")),
                    "stopLossPct": float(os.getenv("AUTO_STOP_LOSS_PCT", "1.0")),
                    "minUsPrice": self.min_us_price,
                    "maxUsPrice": self.max_us_price,
                    "minUsVolume": self.min_us_volume,
                    "rankingBasis": "MARKET_TRADING_VOLUME",
                },
            }
