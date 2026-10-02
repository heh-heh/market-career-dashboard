#!/usr/bin/env python3
import json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
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

def rss(query,limit=6):
    url="https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":query,"hl":"ko","gl":"KR","ceid":"KR:ko"})
    out=[]
    try:
        root=ET.fromstring(get(url))
        for item in root.findall("./channel/item")[:limit]:
            out.append({"title":item.findtext("title",""),"link":item.findtext("link",""),"published":item.findtext("pubDate",""),"source":query})
    except Exception as e:
        out.append({"title":"뉴스 수집 실패","link":"","published":"","source":str(e)})
    return out

now=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
stocks=[stock("005930.KS","삼성전자"),stock("000660.KS","SK하이닉스"),stock("042700.KS","한미반도체"),stock("036570.KS","엔씨소프트")]
news=[]
for q in ["반도체 AI HBM 한국","게임 산업 신작 실적 한국","게임 개발자 채용","백엔드 서버 개발자 채용","AI 개발 신기술"]:
    news += rss(q,4)

data={
 "updatedAt":now,
 "notice":"주가 데이터는 Yahoo Finance 차트 엔드포인트에서 조회한 참고용 데이터이며, 뉴스는 Google News RSS 검색 결과의 링크/제목을 표시합니다. 각 제공자의 이용약관을 확인하세요.",
 "stocks":stocks,
 "news":news[:20],
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
