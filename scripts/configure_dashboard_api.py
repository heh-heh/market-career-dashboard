#!/usr/bin/env python3
"""Verify a public HTTPS dashboard before producing its non-secret Pages config."""
import argparse
import json
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime,timezone

ORIGIN='https://heh-heh.github.io'


def verify(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('PUBLIC_HTTPS_ORIGIN_REQUIRED')
    base=url.rstrip('/')
    def request(path,method='GET'):
        req=urllib.request.Request(base+path,method=method,headers={'Origin':ORIGIN})
        try:
            response=urllib.request.urlopen(req,timeout=10)
        except urllib.error.HTTPError as e: response=e
        with response:
            if response.geturl()!=base+path: raise ValueError('API_REDIRECT_BLOCKED')
            body=response.read(64*1024*1024+1)
            if len(body)>64*1024*1024: raise ValueError('API_RESPONSE_TOO_LARGE')
            return response.status,response.headers,body
    status,headers,raw=request('/api/health')
    data=json.loads(raw)
    if status!=200 or headers.get('Access-Control-Allow-Origin')!=ORIGIN or not data.get('dataReady') or data.get('mode')!='dashboard-only' or data.get('tradingEnabled') is not False:
        raise ValueError('DASHBOARD_HEALTH_CORS_OR_SAFETY_FAILED')
    status,_,raw=request('/api/dashboard')
    data=json.loads(raw)
    if status!=200 or not data.get('stocks') or not data.get('indices') or not data.get('updatedAt'):
        raise ValueError('DASHBOARD_DATA_CHECK_FAILED')
    stamp=datetime.fromisoformat(data['updatedAt'].replace('Z','+00:00'))
    if stamp.tzinfo is None or not -300<=(datetime.now(timezone.utc)-stamp).total_seconds()<=10800:
        raise ValueError('DASHBOARD_DATA_STALE_OR_AMBIGUOUS')
    for path,method in [('/api/trading/status','GET'),('/api/trading/live/arm','POST'),('/api/login','POST'),('/admin.html','GET')]:
        status,_,_=request(path,method)
        if status not in (403,404,405): raise ValueError('PRIVATE_ROUTE_EXPOSED:'+path)
    return base


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True)
    p.add_argument('--out',default='dashboard-config.js')
    a=p.parse_args()
    base=verify(a.url)
    config='window.DASHBOARD_CONFIG = '+json.dumps({'apiBase':base,'publicReadOnly':True})+';\n'
    path=Path(a.out); tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(config); tmp.replace(path)
    print('VERIFIED_PUBLIC_API '+base)

if __name__=='__main__': main()
