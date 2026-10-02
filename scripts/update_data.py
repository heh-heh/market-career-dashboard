#!/usr/bin/env python3
import json, urllib.request, urllib.parse, xml.etree.ElementTree as ET, re
from datetime import datetime, timezone
from pathlib import Path

OUT=Path("data/dashboard.json")
OUT.parent.mkdir(parents=True,exist_ok=True)

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"market-career-dashboard/1.0"})
    with urllib.request.urlopen(req,timeout=20) as r:
        return r.read()

def stock(symbol,name):
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
    try:
        raw=json.loads(get(url))
        res=raw["chart"]["result"][0]
        q=res["indicators"]["quote"][0]
        closes=q["close"]
        prices=[x for x in closes if x is not None]
        current=prices[-1]
        prev=prices[-2] if len(prices)>1 else current
        change=(current-prev)/prev*100 if prev else 0
        return {"symbol":symbol,"name":name,"price":current,"change":change,"source":"Yahoo Finance"}
    except Exception as e:
        return {"symbol":symbol,"name":name,"price":None,"change":None,"source":"unavailable","error":str(e)}

def classify_news(title):
    t=title.lower()
    positive_words=["상승","급등","호재","수주","증가","성장","개선","최대","돌파","투자","확대","흑자","출시"]
    negative_words=["하락","급락","악재","감소","적자","지연","축소","우려","규제","소송","철회"]
    market_words=["반도체","hbm","ai","삼성","sk하이닉스","한미반도체","게임","엔씨","넥슨","크래프톤","서버","클라우드"]
    pos=sum(1 for w in positive_words if w in t)
    neg=sum(1 for w in negative_words if w in t)
    relevance=sum(1 for w in market_words if w in t)
    if pos > neg:
        impact="긍정"
    elif neg > pos:
        impact="부정"
    else:
        impact="중립"
    importance="높음" if relevance >= 2 or abs(pos-neg) >= 2 else ("보통" if relevance else "낮음")
    return {"impact":impact,"importance":importance,"relevance":relevance}

def rss(query,limit=6):
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":query,"hl":"ko","gl":"KR","ceid":"KR:ko"})
    out=[]
    try:
        root=ET.fromstring(get(url))
        for item in root.findall("./channel/item")[:limit]:
            title=item.findtext("title","")
            meta=classify_news(title)
            out.append({
                "title":title,
                "link":item.findtext("link",""),
                "published":item.findtext("pubDate",""),
                "source":query,
                **meta
            })
    except Exception as e:
        out.append({"title":"뉴스 수집 실패","link":"","published":"","source":str(e),"impact":"중립","importance":"낮음","relevance":0})
    return out

now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
stocks=[
    stock("005930.KS","삼성전자"),
    stock("000660.KS","SK하이닉스"),
    stock("042700.KS","한미반도체"),
    stock("036570.KS","엔씨소프트")
]

news=[]
for q in ["반도체 AI HBM 한국","게임 산업 신작 실적 한국","게임 개발자 채용","백엔드 서버 개발자 채용","AI 개발 신기술"]:
    news += rss(q,4)

# 중요도와 최신성 중심으로 주요 뉴스를 우선 배치
news=sorted(news,key=lambda x:(x.get("importance")=="높음",x.get("relevance",0),x.get("published","")),reverse=True)[:20]

data={
 "updatedAt":now,
 "notice":"주가 데이터는 Yahoo Finance 차트 엔드포인트에서 조회한 참고용 데이터이며, 뉴스는 Google News RSS 검색 결과입니다. 뉴스 영향 분류는 키워드 기반 참고용 분석이며 투자 판단의 근거가 아닙니다.",
 "stocks":stocks,
 "news":news,
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
OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
print(f"updated {OUT} at {now}")
