#!/usr/bin/env python3
import json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT=Path("data/dashboard.json")
OUT.parent.mkdir(parents=True,exist_ok=True)

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"market-career-dashboard/1.0"})
    with urllib.request.urlopen(req,timeout=20) as r:
        return r.read()

def quote_symbol(symbol):
    return urllib.parse.quote(symbol,safe="")

def stock(symbol,name,category):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_symbol(symbol)}?range=5d&interval=1d"
    try:
        raw=json.loads(get(url)); res=raw["chart"]["result"][0]
        q=res["indicators"]["quote"][0]; prices=[x for x in q["close"] if x is not None]
        current=prices[-1]; prev=prices[-2] if len(prices)>1 else current
        return {"symbol":symbol,"name":name,"category":category,"price":current,"change":(current-prev)/prev*100 if prev else 0,"source":"Yahoo Finance"}
    except Exception as e:
        return {"symbol":symbol,"name":name,"category":category,"price":None,"change":None,"source":"unavailable","error":str(e)}

def classify(title):
    t=title.lower()
    pos=["상승","급등","호재","수주","증가","성장","개선","최대","돌파","투자","확대","흑자","출시"]
    neg=["하락","급락","악재","감소","적자","지연","축소","우려","규제","소송","철회"]
    rel=["반도체","hbm","ai","삼성","sk하이닉스","한미반도체","엔비디아","nvidia","amd","인텔","intel","마이크론","게임","엔씨","넥슨","크래프톤","서버","클라우드"]
    p=sum(w in t for w in pos); n=sum(w in t for w in neg); r=sum(w in t for w in rel)
    return {"impact":"긍정" if p>n else "부정" if n>p else "중립","importance":"높음" if r>=2 or abs(p-n)>=2 else "보통" if r else "낮음","relevance":r}

def rss(query,limit=6):
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":query+" when:1d","hl":"ko","gl":"KR","ceid":"KR:ko"})
    out=[]
    try:
        root=ET.fromstring(get(url)); cutoff=datetime.now(timezone.utc)-timedelta(days=2)
        for item in root.findall("./channel/item")[:limit]:
            title=item.findtext("title",""); pub=item.findtext("pubDate","")
            try: dt=datetime.strptime(pub,"%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except Exception: dt=None
            if dt and dt < cutoff: continue
            out.append({"title":title,"link":item.findtext("link",""),"published":pub,"source":query,**classify(title)})
    except Exception as e:
        out.append({"title":"뉴스 수집 실패","link":"","published":"","source":str(e),"impact":"중립","importance":"낮음","relevance":0})
    return out

now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

korea=[
 stock("005930.KS","삼성전자","한국 반도체"),stock("000660.KS","SK하이닉스","한국 반도체"),
 stock("042700.KS","한미반도체","한국 반도체"),stock("036570.KS","엔씨소프트","한국 게임")]
us=[
 stock("NVDA","NVIDIA","미국 반도체"),stock("AMD","AMD","미국 반도체"),stock("INTC","Intel","미국 반도체"),
 stock("AVGO","Broadcom","미국 반도체"),stock("MU","Micron","미국 반도체"),stock("TSM","TSMC","미국/글로벌 반도체")]

indices=[
 stock("^KS11","KOSPI","종합 지수"),stock("^KQ11","KOSDAQ","종합 지수"),
 stock("^IXIC","NASDAQ Composite","종합 지수"),stock("^GSPC","S&P 500","종합 지수"),
 stock("^SOX","PHLX Semiconductor Index (SOX)","반도체 종합"),
 stock("SOXX","iShares Semiconductor ETF","반도체 ETF"),stock("SMH","VanEck Semiconductor ETF","반도체 ETF")]

news=[]
for q in ["반도체 AI HBM 한국 미국","NVIDIA AMD Intel semiconductor","게임 산업 신작 실적 한국","게임 개발자 채용","백엔드 서버 개발자 채용","AI 개발 신기술"]:
    news += rss(q,6)
news=sorted(news,key=lambda x:(x.get("importance")=="높음",x.get("relevance",0),x.get("published","")),reverse=True)[:30]

data={
 "updatedAt":now,
 "notice":"주가·지수: Yahoo Finance 참고 데이터. 주식 화면은 페이지가 열린 동안 60초마다 실시간 시세를 재조회합니다. 뉴스 영향은 키워드 기반 1차 분류이며 투자 판단의 근거가 아닙니다.",
 "stocks":korea+us,"indices":indices,"news":news,
 "employment":[
  {"title":"게임 클라이언트","skills":["C++","Unity","Unreal","자료구조/알고리즘","최적화"]},
  {"title":"게임 서버","skills":["C++/Java/Python","REST API","DB","Redis","AWS","네트워크"]},
  {"title":"백엔드","skills":["REST API","DB","Docker","Cloud","테스트"]}],
 "trends":[
  {"title":"생성형 AI","text":"코드 생성·리뷰·테스트·문서화 등 개발 파이프라인 보조"},
  {"title":"AI Agent","text":"여러 단계의 개발 작업을 계획하고 도구를 호출하는 자동화"},
  {"title":"온디바이스 AI","text":"기기에서 추론해 지연시간·비용·데이터 이동을 줄이는 방향"},
  {"title":"게임 라이브서비스","text":"실시간 지표와 운영 자동화, 콘텐츠 배포 및 실험의 중요성"}]}
OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
print(f"updated {OUT} at {now}")