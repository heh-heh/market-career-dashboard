#!/usr/bin/env python3
import json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import time

OUT=Path("data/dashboard.json")
OUT.parent.mkdir(parents=True,exist_ok=True)

def get(url, attempts=3, timeout=12):
    last=None
    for n in range(attempts):
        try:
            req=urllib.request.Request(url,headers={
                "User-Agent":"Mozilla/5.0 market-career-dashboard/1.1",
                "Accept":"application/json,text/plain,*/*"
            })
            with urllib.request.urlopen(req,timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last=e
            if n+1 < attempts:
                time.sleep(1.2*(n+1))
    raise last

def quote_symbol(symbol):
    return urllib.parse.quote(symbol,safe="")

def chart_data(symbol, rng, interval):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_symbol(symbol)}?range={rng}&interval={interval}&includePrePost=false"
    try:
        raw=json.loads(get(url))
        res=raw["chart"]["result"][0]
        q=res["indicators"]["quote"][0]
        ts=res.get("timestamp",[])
        opens=q.get("open",[]); highs=q.get("high",[])
        lows=q.get("low",[]); closes=q.get("close",[])
        vols=q.get("volume",[])
        out=[]
        for i,t in enumerate(ts):
            if i>=len(opens) or i>=len(highs) or i>=len(lows) or i>=len(closes):
                continue
            o,h,l,c=opens[i],highs[i],lows[i],closes[i]
            if None in (o,h,l,c):
                continue
            v=vols[i] if i<len(vols) else None
            out.append({"t":t,"o":o,"h":h,"l":l,"c":c,"v":v})
        return out
    except Exception:
        return []

try:
    previous_data=json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
except Exception:
    previous_data={}

previous_by_symbol={
    x.get("symbol"):x
    for x in previous_data.get("stocks",[])+previous_data.get("indices",[])
    if x.get("symbol")
}

def stock(symbol,name,category):
    previous=previous_by_symbol.get(symbol)
    daily=chart_data(symbol,"1y","1d")

    if not daily and previous and previous.get("history"):
        return {
            **previous,
            "name":name,
            "category":category,
            "source":"Yahoo Finance (cached fallback)",
            "warning":"이번 수집에서 최신 가격 데이터를 받지 못해 직전 정상 데이터를 유지했습니다."
        }

    try:
        prices=[x["c"] for x in daily if x.get("c") is not None]
        if len(prices)<2:
            raise RuntimeError("daily price data unavailable")

        current=prices[-1]
        prev=prices[-2]
        short=prices[-20:] if len(prices)>=20 else prices
        long=prices[-120:] if len(prices)>=120 else prices

        def trend(arr):
            return (arr[-1]-arr[0])/arr[0] if len(arr)>1 and arr[0] else 0

        st=trend(short)
        lt=trend(long)
        rs=[(short[i]-short[i-1])/short[i-1] for i in range(1,len(short)) if short[i-1]]
        vol=(sum(r*r for r in rs)/len(rs))**0.5 if rs else 0
        short_base=current*(1+st*0.35)
        short_band=current*vol*2.0
        long_base=current*(1+lt*0.55)

        candles={
            "1m":chart_data(symbol,"1d","1m"),
            "5m":chart_data(symbol,"5d","5m"),
            "1h":chart_data(symbol,"1mo","1h"),
            "1d":daily[-252:]
        }

        if previous and previous.get("candles"):
            for key in candles:
                if not candles[key] and previous["candles"].get(key):
                    candles[key]=previous["candles"][key]

        return {
            "symbol":symbol,
            "name":name,
            "category":category,
            "price":current,
            "change":(current-prev)/prev*100 if prev else 0,
            "source":"Yahoo Finance",
            "history":prices[-252:],
            "candles":candles,
            "forecast":{
                "short":{
                    "period":"단기 1~4주",
                    "base":short_base,
                    "bull":short_base+short_band,
                    "bear":max(0,short_base-short_band),
                    "trend":"상승" if st>0.03 else "하락" if st<-0.03 else "중립"
                },
                "long":{
                    "period":"장기 6~12개월",
                    "base":long_base,
                    "trend":"상승" if lt>0.08 else "하락" if lt<-0.08 else "중립"
                },
                "method":"최근 가격 추세와 변동성을 이용한 참고용 시나리오이며 확정적인 주가 예측이 아닙니다."
            }
        }
    except Exception:
        if previous:
            return {
                **previous,
                "name":name,
                "category":category,
                "source":"Yahoo Finance (cached fallback)",
                "warning":"수집 오류로 직전 정상 데이터를 유지했습니다."
            }
        return {
            "symbol":symbol,
            "name":name,
            "category":category,
            "price":None,
            "change":None,
            "source":"unavailable"
        }

def classify(title):
    t=title.lower()
    pos=["상승","급등","호재","수주","증가","성장","개선","최대","돌파","투자","확대","흑자","출시"]
    neg=["하락","급락","악재","감소","적자","지연","축소","우려","규제","소송","철회"]
    rel=["반도체","hbm","ai","삼성","sk하이닉스","한미반도체","엔비디아","nvidia","amd","인텔","intel","마이크론","게임","엔씨","넥슨","크래프톤","서버","클라우드"]
    p=sum(w in t for w in pos)
    n=sum(w in t for w in neg)
    r=sum(w in t for w in rel)
    return {
        "impact":"긍정" if p>n else "부정" if n>p else "중립",
        "importance":"높음" if r>=2 or abs(p-n)>=2 else "보통" if r else "낮음",
        "relevance":r
    }

def rss(query,limit=6,days=2):
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode({
        "q":query+" when:"+str(days)+"d",
        "hl":"ko",
        "gl":"KR",
        "ceid":"KR:ko"
    })
    out=[]
    try:
        root=ET.fromstring(get(url))
        cutoff=datetime.now(timezone.utc)-timedelta(days=days)
        for item in root.findall("./channel/item")[:limit]:
            title=item.findtext("title","")
            pub=item.findtext("pubDate","")
            try:
                dt=datetime.strptime(pub,"%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except Exception:
                dt=None
            if dt and dt < cutoff:
                continue
            out.append({
                "title":title,
                "link":item.findtext("link",""),
                "published":pub,
                "publishedAt":dt.isoformat() if dt else "",
                "description":item.findtext("description",""),
                "source":query,
                **classify(title)
            })
    except Exception:
        pass
    return out

def normalize_text(value):
    import re
    return re.sub(r"[^0-9a-z가-힣]+","",str(value or "").lower())

def classify_job(title, description):
    t=(title+" "+description).lower()
    if any(x in t for x in ["인턴","intern","internship"]):
        career="인턴"
    elif any(x in t for x in ["신입/경력","신입·경력","신입 경력"]):
        career="신입/경력"
    elif any(x in t for x in ["신입","주니어","junior","new grad","entry level","entry-level"]):
        career="신입"
    else:
        import re
        m=re.search(r"(?:경력|experience)\s*(?:\(|:)?\s*(\d+)\s*[~\-]?\s*(\d+)?\s*년",t)
        career=(m.group(1)+"년+" if m else "경력")
    if any(x in t for x in ["클라이언트","client","unity","unreal","언리얼","c++","게임 클라이언트"]):
        area="클라이언트"
    elif any(x in t for x in ["서버","server","backend","back-end","백엔드","api","네트워크"]):
        area="백엔드/서버"
    elif any(x in t for x in ["프론트","frontend","front-end","react","vue","웹 개발"]):
        area="프론트엔드"
    elif any(x in t for x in ["ai","머신러닝","machine learning","ml","데이터"]):
        area="AI/데이터"
    else:
        area="개발"
    stack_candidates=[
        ("C++","c++"),("C#","c#"),("Java","java"),("Python","python"),
        ("JavaScript","javascript"),("TypeScript","typescript"),("Go","golang"),
        ("Rust","rust"),("Unity","unity"),("Unreal","unreal"),
        ("AWS","aws"),("Azure","azure"),("GCP","gcp"),("Docker","docker"),
        ("Kubernetes","kubernetes"),("Spring","spring"),("React","react"),
        ("Node.js","node.js"),("Redis","redis"),("MySQL","mysql"),
        ("PostgreSQL","postgresql"),("MongoDB","mongodb"),("Git","git"),
        ("Linux","linux")
    ]
    stack=[label for label,key in stack_candidates if key in t]
    return {"area":area,"career":career,"skills":stack[:12]}

def job_source(link):
    host=urllib.parse.urlparse(link).netloc.lower()
    if "wanted.co.kr" in host: return "원티드"
    if "saramin.co.kr" in host: return "사람인"
    if "jobkorea.co.kr" in host: return "잡코리아"
    if "jumpit.saramin.co.kr" in host: return "점핏"
    if "rocketpunch.com" in host: return "로켓펀치"
    if "career.programmers.co.kr" in host: return "프로그래머스"
    if "linkedin.com" in host: return "LinkedIn"
    return host.replace("www.","") or "채용 사이트"

def extract_company(title, description):
    import re
    patterns=[
        r"^(.+?)\s+(?:채용|모집|공고)",
        r"^(.+?)\s*[-|·]\s*(?:신입|경력|인턴|개발자|채용)",
        r"(?:회사|기업)\s*[:：]\s*([^|·,]+)"
    ]
    for p in patterns:
        m=re.search(p,title,re.I)
        if m:
            value=m.group(1).strip(" -|·")
            if 1 < len(value) <= 60:
                return value
    for line in re.sub(r"<[^>]+>"," ",description or "").splitlines():
        m=re.search(r"(?:회사|기업)\s*[:：]\s*([^|·,]+)",line,re.I)
        if m:
            return m.group(1).strip()
    return ""

def clean_html(text):
    import re, html
    text=html.unescape(text or "")
    text=re.sub(r"<script[\s\S]*?</script>"," ",text,flags=re.I)
    text=re.sub(r"<style[\s\S]*?</style>"," ",text,flags=re.I)
    text=re.sub(r"<[^>]+>"," ",text)
    return re.sub(r"\s+"," ",text).strip()

def fetch_job_detail(url):
    try:
        raw=get(url,attempts=1,timeout=7).decode("utf-8","ignore")
    except Exception:
        return {}
    text=clean_html(raw)
    return {"detailText":text[:12000]}

def parse_job_detail(item):
    detail=fetch_job_detail(item.get("link",""))
    text=detail.get("detailText","")
    title=(item.get("title","")+" "+text).strip()
    meta=classify_job(title,text)
    import re
    skill_patterns=[
        ("C++",r"\bc\+\+\b"),("C#",r"\bc#\b"),("Java",r"\bjava\b"),
        ("Kotlin",r"\bkotlin\b"),("Python",r"\bpython\b"),("JavaScript",r"javascript"),
        ("TypeScript",r"typescript"),("Go",r"\bgolang\b"),("Rust",r"\brust\b"),
        ("Unity",r"unity"),("Unreal",r"unreal|언리얼"),("DirectX",r"directx"),
        ("OpenGL",r"opengl"),("UE5",r"unreal engine 5|ue5"),("AWS",r"\baws\b"),
        ("Azure",r"\bazure\b"),("GCP",r"\bgcp\b"),("Docker",r"docker"),
        ("Kubernetes",r"kubernetes"),("Spring",r"spring"),("React",r"react"),
        ("Node.js",r"node\.?js"),("Redis",r"redis"),("MySQL",r"mysql"),
        ("PostgreSQL",r"postgresql|postgre"),("MongoDB",r"mongodb"),("Git",r"\bgit\b"),
        ("Linux",r"linux"),("WebSocket",r"websocket"),("REST API",r"rest\s*api"),
        ("TCP/UDP",r"tcp\s*/?\s*udp"),("Unreal Blueprint",r"blueprint")
    ]
    skills=[name for name,pat in skill_patterns if re.search(pat,text,re.I)]
    career=meta["career"]
    m=re.search(r"(?:경력사항|경력|experience)\s*[:：]?\s*(경력무관|신입[·/]?경력|신입|인턴|\d+\s*[~\-]?\s*\d*\s*년(?:\s*이상|\s*이하)?)",text,re.I)
    if m:
        raw=m.group(1).strip()
        if "무관" in raw: career="경력무관"
        elif "인턴" in raw.lower(): career="인턴"
        elif "신입" in raw and "경력" in raw: career="신입/경력"
        elif "신입" in raw: career="신입"
        elif "년" in raw: career=raw
        else: career="경력"
    employment=""
    for term in ["정규직","계약직","인턴","프리랜서","병역특례","아르바이트"]:
        if term in text:
            employment += (", " if employment else "")+term
    deadline=""
    m=re.search(r"(?:마감일|접수기간)\s*[:：]?\s*([^\n]{2,50})",text,re.I)
    if m: deadline=m.group(1).strip()
    return {
        "area":meta["area"],"career":career,"skills":skills[:18],
        "employmentType":employment,"deadline":deadline,
        "detailText":text[:6000]
    }

def collect_employment_news(previous):
    now=datetime.now(timezone.utc)
    previous_updated=previous.get("employmentUpdatedAt","")
    try:
        last=datetime.fromisoformat(previous_updated.replace("Z","+00:00"))
    except Exception:
        last=datetime.min.replace(tzinfo=timezone.utc)
    if (now-last).total_seconds() < 1800:
        return previous.get("employmentNews",[]), previous_updated

    queries=[
        "게임 개발자 채용 C++ Unity Unreal",
        "게임 서버 개발자 채용",
        "백엔드 개발자 채용 Java Python AWS",
        "신입 개발자 채용 게임 IT",
        "개발자 채용 공고 취업"
    ]
    fresh=[]
    for q in queries:
        fresh += rss(q,8,days=2)

    cutoff=now-timedelta(days=2)
    merged={}
    for item in previous.get("employmentNews",[])+fresh:
        stamp=item.get("publishedAt","")
        try:
            dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        except Exception:
            dt=None
        if dt and dt>=cutoff:
            key=(item.get("link") or item.get("title","")).strip()
            if key:
                merged[key]=item

    items=sorted(
        merged.values(),
        key=lambda x:x.get("publishedAt",""),
        reverse=True
    )[:50]
    return items, now.isoformat(timespec="seconds")

def direct_job_list(url, site_name, limit=80, keywords=None):
    import re, html as htmlmod
    out=[]
    try:
        raw=get(url,attempts=2,timeout=12).decode("utf-8","ignore")
    except Exception:
        return out
    # GameJob/JobKorea-style HTML: extract real detail links and nearby visible text.
    pat=re.compile(r'href=["\']([^"\']*(?:GI_Read/View|Recruit/GI_Read|Recruit/GI_Read/View|Recruit/GI_Read/View)[^"\']*)["\'][^>]*>(.*?)</a>',re.I|re.S)
    seen=set()
    for m in pat.finditer(raw):
        link=urllib.parse.urljoin(url,htmlmod.unescape(m.group(1)))
        title=clean_html(m.group(2))
        if not title or len(title)<3 or link in seen:
            continue
        seen.add(link)
        if keywords and not any(k.lower() in (title+" "+clean_html(raw[max(0,m.start()-900):m.end()+900])).lower() for k in keywords):
            continue
        context=clean_html(raw[max(0,m.start()-1200):m.end()+1800])
        company=""
        cm=re.search(r'(?:기업명|회사명)[^가-힣A-Za-z0-9]{0,20}([^<|]{2,80})',context,re.I)
        if cm:
            company=clean_html(cm.group(1)).strip(" -|·")
        if not company:
            # Common GameJob structure has company anchor immediately before the job title.
            prev=clean_html(raw[max(0,m.start()-2200):m.start()])
            parts=[x.strip() for x in re.split(r'\\s{2,}|\\n',prev) if x.strip()]
            if parts:
                company=parts[-1][-80:]
        area=classify_job(title,context)
        out.append({
            "title":title,
            "company":company or "기업명 확인 필요",
            "source":site_name,
            "sourceSite":site_name,
            "link":link,
            "description":context[:3000],
            "published":datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "publishedAt":datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "area":area["area"],
            "career":area["career"],
            "skills":area["skills"]
        })
        if len(out)>=limit:
            break
    return out

def collect_job_postings(previous):
    now=datetime.now(timezone.utc)
    previous_updated=previous.get("jobPostingsUpdatedAt","")
    try:
        last=datetime.fromisoformat(previous_updated.replace("Z","+00:00"))
    except Exception:
        last=datetime.min.replace(tzinfo=timezone.utc)
    # Refresh job postings on every scheduled run.

    fresh=[]
    # GameJob has a server-rendered public recruitment list with pagination.
    # Collect several pages and focus on development/server/client/engine/backend roles.
    game_keywords=[
        "프로그래머","클라이언트","서버","네트워크","엔진","백엔드",
        "개발","C++","C#","Unity","Unreal","언리얼","게임","테크"
    ]
    for page in range(1,7):
        url="https://www.gamejob.co.kr/Recruit/joblist" if page==1 else f"https://www.gamejob.co.kr/recruit/_GI_Job_List?Page={page}"
        page_jobs=direct_job_list(url,"게임잡",limit=80,keywords=game_keywords)
        print(f"job source gamejob page={page} count={len(page_jobs)}")
        fresh += page_jobs

    # Keep RSS discovery as a broad fallback across major hiring sites.
    sites=[
        ("site:gamejob.co.kr/Recruit/GI_Read 게임 프로그래머", "게임잡"),
        ("site:gamejob.co.kr/Recruit/GI_Read 클라이언트 프로그래머", "게임잡"),
        ("site:gamejob.co.kr/Recruit/GI_Read 서버 프로그래머", "게임잡"),
        ("site:gamejob.co.kr/Recruit/GI_Read C++ Unity Unreal", "게임잡"),
        ("site:gamejob.co.kr/Recruit/GI_Read 신입 프로그래머", "게임잡"),
        ("site:wanted.co.kr 게임 개발자 채용", "원티드"),
        ("site:wanted.co.kr 서버 백엔드 개발자 채용", "원티드"),
        ("site:wanted.co.kr C++ Unity Unreal 개발자", "원티드"),
        ("site:wanted.co.kr 신입 개발자 채용", "원티드"),
        ("site:saramin.co.kr 게임 개발자 채용", "사람인"),
        ("site:saramin.co.kr 백엔드 서버 개발자 채용", "사람인"),
        ("site:saramin.co.kr C++ Unity 개발자", "사람인"),
        ("site:saramin.co.kr 신입 개발자 채용", "사람인"),
        ("site:jobkorea.co.kr 게임 개발자 채용", "잡코리아"),
        ("site:jobkorea.co.kr 서버 백엔드 개발자 채용", "잡코리아"),
        ("site:jobkorea.co.kr 신입 개발자 채용", "잡코리아"),
        ("site:jobkorea.co.kr C++ Unity 개발자", "잡코리아"),
        ("site:jumpit.saramin.co.kr 개발자 채용", "점핏"),
        ("site:jumpit.saramin.co.kr 백엔드 C++ 개발자", "점핏"),
        ("site:jumpit.saramin.co.kr 신입 개발자", "점핏"),
        ("site:rocketpunch.com/jobs 개발자 채용", "로켓펀치"),
        ("site:rocketpunch.com/jobs 백엔드 개발자", "로켓펀치"),
        ("site:career.programmers.co.kr 개발자 채용", "프로그래머스"),
        ("site:career.programmers.co.kr 게임 개발자", "프로그래머스")
    ]

    existing_links={x.get("link") for x in previous.get("jobPostings",[])}
    for q,site_name in sites:
        for item in rss(q,20,days=14):
            link=item.get("link","")
            desc=item.get("description","")
            title=item.get("title","").strip()
            meta=classify_job(title,desc)
            company=extract_company(title,desc)
            fresh.append({
                "title":title,
                "company":company or "기업명 확인 필요",
                "source":site_name,
                "link":link,
                "description":desc,
                "published":item.get("published",""),
                "publishedAt":item.get("publishedAt",""),
                "area":meta["area"],
                "career":meta["career"],
                "skills":meta["skills"],
                "sourceSite":site_name,
            })

    cutoff=now-timedelta(days=30)
    candidates=previous.get("jobPostings",[])+fresh

    enriched=[]
    for item in candidates:
        if item.get("link") in existing_links and item.get("skills"):
            enriched.append(item)
            continue
        detail=parse_job_detail(item)
        item={**item,**{k:v for k,v in detail.items() if v}}
        enriched.append(item)
    candidates=enriched

    def role_tokens(title, company):
        import re
        text=normalize_text(title)
        comp=normalize_text(company)
        for token in ["채용","모집","공고","개발자","인턴","신입","경력","잡코리아","사람인","원티드","점핏","프로그래머스","로켓펀치","게임잡","sw","개발"]:
            text=text.replace(token,"")
        if comp:
            text=text.replace(comp,"")
        return set(re.findall(r"[0-9a-z가-힣]{2,}",text))

    grouped={}
    for item in candidates:
        stamp=item.get("publishedAt","")
        try:
            dt=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        except Exception:
            dt=None
        if not dt or dt<cutoff:
            continue

        company=normalize_text(item.get("company",""))
        role=role_tokens(item.get("title",""),item.get("company",""))
        if not role:
            role={normalize_text(item.get("title",""))}

        match_key=None
        for key,existing in grouped.items():
            existing_company=normalize_text(existing.get("company",""))
            if company and company==existing_company:
                existing_role=role_tokens(existing.get("title",""),existing.get("company",""))
                union=role|existing_role
                overlap=len(role&existing_role)/len(union) if union else 0
                if overlap>=0.45 or not existing_role:
                    match_key=key
                    break

        if match_key is None:
            match_key=(company or "unknown")+"|"+normalize_text(item.get("title",""))[:140]+"|"+str(len(grouped))
            item=dict(item)
            item["sources"]=[{"site":item.get("sourceSite") or item.get("source",""),"link":item.get("link","")}]
            grouped[match_key]=item
        else:
            existing=grouped[match_key]
            existing_sources=existing.setdefault("sources",[])
            if not any(x.get("link")==item.get("link") for x in existing_sources):
                existing_sources.append({"site":item.get("sourceSite") or item.get("source",""),"link":item.get("link","")})
            existing["skills"]=sorted(set(existing.get("skills",[])+item.get("skills",[])))[:18]
            if not existing.get("detailText") and item.get("detailText"):
                existing["detailText"]=item["detailText"]
            if not existing.get("deadline") and item.get("deadline"):
                existing["deadline"]=item["deadline"]
            if not existing.get("employmentType") and item.get("employmentType"):
                existing["employmentType"]=item["employmentType"]

    items=sorted(grouped.values(),key=lambda x:x.get("publishedAt",""),reverse=True)[:200]
    return items, now.isoformat(timespec="seconds")

now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
employment_news, employment_updated_at=collect_employment_news(previous_data)
job_postings, job_postings_updated_at=collect_job_postings(previous_data)


korea=[
    stock("005930.KS","삼성전자","한국 반도체"),
    stock("000660.KS","SK하이닉스","한국 반도체"),
    stock("042700.KS","한미반도체","한국 반도체"),
    stock("036570.KS","엔씨소프트","한국 게임")
]

us=[
    stock("NVDA","NVIDIA","미국 반도체"),
    stock("AMD","AMD","미국 반도체"),
    stock("INTC","Intel","미국 반도체"),
    stock("AVGO","Broadcom","미국 반도체"),
    stock("MU","Micron","미국 반도체"),
    stock("TSM","TSMC","미국/글로벌 반도체")
]

indices=[
    stock("^KS11","KOSPI","종합 지수"),
    stock("^KQ11","KOSDAQ","종합 지수"),
    stock("^IXIC","NASDAQ Composite","종합 지수"),
    stock("^GSPC","S&P 500","종합 지수"),
    stock("^SOX","PHLX Semiconductor Index (SOX)","반도체 종합"),
    stock("SOXX","iShares Semiconductor ETF","반도체 ETF"),
    stock("SMH","VanEck Semiconductor ETF","반도체 ETF")
]

news=[]
for q in [
    "반도체 AI HBM 한국 미국",
    "NVIDIA AMD Intel semiconductor",
    "게임 산업 신작 실적 한국",
    "AI 개발 신기술"
]:
    news += rss(q,6,days=2)

news=sorted(
    news,
    key=lambda x:(x.get("importance")=="높음",x.get("relevance",0),x.get("publishedAt","")),
    reverse=True
)[:30]

data={
    "updatedAt":now,
    "notice":"주가·지수: Yahoo Finance 참고 데이터. 캔들은 1분·5분·1시간·일봉으로 수집하며 제공처 지연이 있을 수 있습니다. 페이지는 60초마다 데이터를 재조회합니다. 뉴스 영향은 키워드 기반 1차 분류이며 투자 판단의 근거가 아닙니다.",
    "stocks":korea+us,
    "indices":indices,
    "news":news,
    "employmentNews":employment_news,
    "employmentUpdatedAt":employment_updated_at,
    "jobPostings":job_postings,
    "jobPostingsUpdatedAt":job_postings_updated_at,
    "employment":[
        {"title":"게임 클라이언트","skills":["C++","Unity","Unreal","자료구조/알고리즘","최적화"]},
        {"title":"게임 서버","skills":["C++/Java/Python","REST API","DB","Redis","AWS","네트워크"]},
        {"title":"백엔드","skills":["REST API","DB","Docker","Cloud","테스트"]}
    ],
    "trends":[
        {"title":"생성형 AI","text":"코드 생성·리뷰·테스트·문서화 등 개발 파이프라인 보조"},
        {"title":"AI Agent","text":"여러 단계의 개발 작업을 계획하고 도구를 호출하는 자동화"},
        {"title":"온디바이스 AI","text":"기기에서 추론해 지연시간·비용·데이터 이동을 줄이는 방향"},
        {"title":"게임 라이브서비스","text":"실시간 지표와 운영 자동화, 콘텐츠 배포 및 실험의 중요성"}
    ]
}

all_quotes=data["stocks"]+data["indices"]
successful=[x for x in all_quotes if isinstance(x.get("price"),(int,float))]
candle_ready=sum(
    1 for x in all_quotes
    if any(x.get("candles",{}).get(k) for k in ("1m","5m","1h","1d"))
)

if len(successful)<10:
    raise RuntimeError(
        f"data quality guard: only {len(successful)}/{len(all_quotes)} quotes available; refusing to overwrite dashboard.json"
    )

data["quality"]={
    "quotes_ok":len(successful),
    "quotes_total":len(all_quotes),
    "candle_ready":candle_ready,
    "generatedAt":now
}

tmp=OUT.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
tmp.replace(OUT)
print(f"updated {OUT} at {now}; quotes={len(successful)}/{len(all_quotes)} candle_ready={candle_ready} job_postings={len(job_postings)}")
