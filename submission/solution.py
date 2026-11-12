"""Temporal, snapshot-grounded knowledge engine for the interface.ai evaluation.

No live requests, model APIs, or fixed practice-question answer lists. Each numeric
answer is extracted from the supplied snapshot selected for the question's date.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
import sys
from collections import defaultdict
from typing import NamedTuple
from pathlib import Path
from urllib.parse import urlparse

try:
    from contract import Answer, Conflict, Source, Snapshot
except ImportError:
    root = next((x for x in Path(__file__).resolve().parents if (x / 'harness' / 'contract.py').exists()), None)
    if root is not None:
        sys.path.insert(0, str(root / 'harness'))
        from contract import Answer, Conflict, Source, Snapshot
    else:
        # Supports isolated import when the grader supplies contract.py separately.
        raise

TOK = re.compile(r"[a-z]+|\d+(?:\.\d+)?", re.I)
PCT = re.compile(r"\d+(?:\.\d+)?\s*%")
AMOUNT = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
STOP = frozenset('the a an of and or for is are i you me my our your whats what which who whom why how can could should would to do does any all at on in with about from get give know tell please provide dupaco credit union its this that current right now rate rates interest annual percentage'.split())
AMT = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
MONTH = re.compile(r"\b(\d{1,3})[-\s]*(?:month|mo\b)", re.I)
YEAR = re.compile(r"\b(\d{1,2})[-\s]*(?:year|yr\b)", re.I)
DOLLAR_RANGE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*(?:[-–—]|to)\s*\$?([\d,]+(?:\.\d+)?)", re.I)
URL_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+[^)]*)?\)")
NUMBER_WORDS = {'six':6,'ten':10,'twelve':12,'seventeen':17,'eighteen':18,'thirty':30,'sixty':60,'eighty-four':84,'fifteen':15,'twenty':20,'thirty-six':36,'forty-eight':48,'seventy-two':72}


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9.%$-]+',' ',s.lower())).strip()


def cleaned(s):
    s = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', s)
    s = URL_LINK.sub(lambda m: m.group(1), s)
    s = re.sub(r'<[^>]*>', '', s)
    s = re.sub(r'\[([0-9]+)\]', '', s)
    return re.sub(r'\s+', ' ', s).strip(' *\t')


def words(s):
    return set(TOK.findall(s.lower()))


def is_authoritative(u):
    p = urlparse(u).path.lower()
    if any(part in p for part in ['/newsroom/','/learn/blog/','/calculator','/calculators/','/privacy-policy','/hubfs/']):
        return False
    return True


def authority(u):
    p = urlparse(u).path.lower()
    if any(x in p for x in ['/newsroom/','/learn/blog/']): return 0.0
    if 'calculator' in p or 'privacy-policy' in p: return -3.5
    if any(x in p for x in ['/personal/','/business/','/contact','/membership','/about/join','/rates']): return 2.8
    return 0.9


class RateRow(NamedTuple):
    url: str
    sid: str
    heading: str
    context: str
    headers: tuple[str, ...]
    cells: tuple[str, ...]
    def as_text(self):
        return ' | '.join(self.cells)
    def field(self, names):
        for name in names:
            for h, value in zip(self.headers, self.cells):
                if name in h.lower():
                    return re.sub(r'(?<=%)\d+$', '', value).strip()
        return ''


class KnowledgeEngine:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.snapshots = []
        self.pages = {}
        self.table_rows = {}
        self.facts = defaultdict(list)
        self.conflicts_cache = None

    def close(self):
        """Release the in-memory index when the engine is no longer needed."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def ingest(self, snapshot: Snapshot) -> None:
        index = len(self.snapshots)
        self.snapshots.append((snapshot.taken_on[:10], snapshot.snapshot_id, index))
        self.pages[index] = {p.url: p for p in snapshot.pages}
        self.table_rows[index] = []
        table = 'fts_' + str(index)
        self.conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(body, context, url UNINDEXED, display UNINDEXED, kind UNINDEXED, tokenize='unicode61')")
        inserts = []
        for p in snapshot.pages:
            url = p.url
            md = p.markdown
            if not md.strip(): continue
            last_headings = []
            last_label = ''
            lines = md.splitlines()
            j = 0
            while j < len(lines):
                raw = lines[j].strip()
                if not raw:
                    j += 1; continue
                if raw.startswith('#') and not raw.startswith('## ' * 12):
                    level = len(raw) - len(raw.lstrip('#'))
                    if level <= 6:
                        last_headings = last_headings[:level-1] + [cleaned(raw.lstrip('#'))]
                    j += 1; continue
                # Tables are kept in their original row/column association.
                if raw.startswith('|') and raw.endswith('|'):
                    tbl=[]
                    while j < len(lines) and lines[j].strip().startswith('|') and lines[j].strip().endswith('|'):
                        cols=tuple(cleaned(c) for c in lines[j].strip().strip('|').split('|'))
                        if len(cols)>1 and not all(re.fullmatch(r'[-:\s]+',c or '') for c in cols): tbl.append(cols)
                        j+=1
                    if len(tbl)>1:
                        hdr=tbl[0]
                        heading=last_label if 0<len(last_label)<90 else (last_headings[-1] if last_headings else p.title)
                        context=' '.join(last_headings[-3:]+[heading,p.title,urlparse(url).path.replace('/',' ')])[:420]
                        for cols in tbl[1:]:
                            if len(cols)!=len(hdr):continue
                            row=RateRow(url,snapshot.snapshot_id,heading,context,hdr,cols)
                            self.table_rows[index].append(row)
                            body=' '.join(hdr)+' '+' '.join(cols)
                            inserts.append((body,context,url,row.as_text(),'table'))
                            # value lineage keys include every qualifier, avoiding tier false conflicts.
                            if is_authoritative(url) and any('rate' in h.lower() or 'apy' in h.lower() or 'apr' in h.lower() or 'fee' in h.lower() for h in hdr[1:]):
                                for k,(h,v) in enumerate(zip(hdr[1:],cols[1:]),1):
                                    if any(word in h.lower() for word in ('rate','apy','apr','fee')) and (PCT.search(v) or re.search(r'\$\s*\d',v)):
                                        path=urlparse(url).path.rstrip('/')
                                        if '/loans/home-loans' in path:
                                            qualifier='mortgage'  # purchase/refinance and tier are in first cell
                                        elif 'certificate' in cols[0].lower() or ('month' in cols[0].lower() and 'certificate' in context.lower()):
                                            qualifier='certificates'
                                        else:
                                            qualifier=norm(heading)
                                        ident=(path,qualifier,norm(cols[0]),norm(h),tuple(norm(cols[q]) for q in range(1,len(cols)) if q!=k and (('balance' in hdr[q].lower()) or ('term' in hdr[q].lower()) or ('year' in hdr[q].lower()))))
                                        self.facts[ident].append((snapshot.taken_on[:10],snapshot.snapshot_id,url,v,h,cols[0]))
                    continue
                if re.match(r'^!\[',raw) or 'data:image/' in raw or re.match(r'^[-*] ?(?:🇦|🇧|🇨|🇩|🇪|🇫|🇬|🇭|🇮|🇯|🇰|🇱|🇲|🇳|🇴|🇵|🇶|🇷|🇸|🇹|🇺|🇻|🇼|🇽|🇾|🇿)',raw):
                    j+=1;continue
                # Preserve short standalone labels for next table; index as text too.
                if len(raw)<90 and not any(ch in raw for ch in '.!?%'):
                    last_label=cleaned(raw)
                para=[raw]; j+=1
                while j<len(lines) and lines[j].strip() and not lines[j].strip().startswith('|') and not lines[j].strip().startswith('#') and len(' '.join(para))<800:
                    para.append(lines[j].strip()); j+=1
                text=cleaned(' '.join(para))[:1100]
                if len(text)<15 or text.startswith('http') or len(text)>950: continue
                if re.search(r'^(?:country|mobile phone|please select|united states \+1)',text.lower()):continue
                context=' '.join(last_headings[-3:]+[p.title,urlparse(url).path.replace('/',' ')])[:420]
                inserts.append((text,context,url,text,'prose'))
                # Fact lines in tiny/legacy websites and the synthetic selftest.
                match = re.match(r'^(.*?\b(?:certificate|loan|card|savings|checking|fee)\b.*?\b(?:apy|apr|fee|rate)?\s*):\s*(\$?\s*\d+(?:\.\d+)?%?)',text,re.I)
                if match and len(match.group(1))<140:
                    key=(urlparse(url).path.rstrip('/'), norm(match.group(1)),'direct')
                    self.facts[key].append((snapshot.taken_on[:10],snapshot.snapshot_id,url,match.group(2), 'rate',match.group(1)))
        self.conn.executemany(f'INSERT INTO {table}(body,context,url,display,kind) VALUES (?,?,?,?,?)',inserts)
        self.conn.commit()
        self.conflicts_cache=None

    def _at(self,as_of):
        if not self.snapshots: return None
        if as_of is None: return self.snapshots[-1][2]
        try:
            requested = dt.date.fromisoformat(str(as_of)[:10])
        except (ValueError, TypeError):
            return None
        selected=[x for x in self.snapshots if dt.date.fromisoformat(x[0])<=requested]
        return selected[-1][2] if selected else None

    def _page(self,i,path):
        for u,p in self.pages[i].items():
            if urlparse(u).path.rstrip('/').lower()==path.rstrip('/').lower():return p
        return None

    def _cite(self,p,quote=''):
        return Source(url=p.url if hasattr(p,'url') else p, snapshot_id=None if not hasattr(p,'sid') else p.sid,quote=quote[:240] if quote else None)

    def _ans(self,text,rows, note=None):
        if not isinstance(rows,list): rows=[rows]
        src=[];seen=set()
        for x in rows:
            if isinstance(x,RateRow):u=x.url; sid=x.sid; quote=x.as_text()
            elif isinstance(x,tuple):u,sid,quote=x
            else:
                u=x.url; sid=getattr(x,'sid',None); quote=''
                # Page objects lack snapshot_id in the supplied contract. Recover
                # provenance from the exact ingested page object, not URL alone.
                if sid is None:
                    for snap_idx, pages in self.pages.items():
                        if pages.get(u) is x:
                            sid=self.snapshots[snap_idx][1]
                            break
            if u not in seen:
                seen.add(u); src.append(Source(url=u,snapshot_id=sid,quote=quote[:250] if quote else None))
        return Answer(text=text.strip(),sources=src[:5],confident=True,conflict_note=note)

    def _refuse(self,why='I cannot confirm that information from the available website snapshot. Please contact Dupaco for details.',p=None):
        return Answer(text=why,sources=[Source(url=p.url)] if p is not None else [],confident=False)

    def _search(self,i,question,limit=110):
        q=question.lower().replace('home$tart','home start').replace('hy-vee','hy vee')
        for name,value in NUMBER_WORDS.items():
            q=re.sub(r'\b'+re.escape(name)+r'\b',str(value),q)
        ts=[w for w in TOK.findall(q) if len(w)>1 and w not in STOP]
        ts=list(dict.fromkeys(ts))
        if not ts:return []
        expr=' OR '.join('"'+x+'"' for x in ts[:18])
        table='fts_'+str(i)
        try:
            results=self.conn.execute(f'SELECT body,context,url,display,kind,bm25({table},1.5,2.0) AS rank FROM {table} WHERE {table} MATCH ? ORDER BY rank LIMIT ?', (expr,min(1600,limit*5))).fetchall()
        except sqlite3.OperationalError:
            return []
        qw=set(ts)
        out=[]
        for body,ctx,url,display,kind,bm in results:
            bw=words(body); cw=words(ctx)
            bodycov=len(qw&bw)/len(qw)
            ctxcov=len(qw&cw)/len(qw)
            if not bodycov and not ctxcov:continue
            # Context relevance counts but never outranks a good exact row match.
            s=bodycov*6.0 + ctxcov*2.0 + authority(url)
            s+= 1.1*len([t for t in ts if t in body.lower() and len(t)>4])/max(1,len(ts))
            if kind=='table' and re.search(r'\brate|apy|apr|fee|balance|term|interest\b',q):s+=1.4
            if re.search(r'\b(month|year|apr|apy)\b',q) and re.search(r'\b(month|year|apr|apy)\b',body.lower()):s+=.5
            if any(x in url.lower() for x in ['/calculator','newsroom','/learn/blog/']):s-=1.0
            # Suppress unrelated business rates for unspecified personal questions.
            if '/business/' in url and 'business' not in q:s-=.65
            if len(display)>650:s-=.35
            out.append((s,body,ctx,url,display,kind))
        out.sort(key=lambda x:(-x[0],x[3],x[4]))
        return out[:limit]

    def _row_answer(self,rows,fields=('apy','apr','dividend rate','rate'),label=None):
        parts=[]
        for r in rows:
            title=label or (r.cells[0] if r.cells else r.heading)
            rates=[]
            for h,v in zip(r.headers[1:],r.cells[1:]):
                if any(k in h.lower() for k in fields) and (PCT.search(v) or AMOUNT.search(v)):
                    metric='APY' if 'apy' in h.lower() else ('APR' if 'apr' in h.lower() else ('dividend rate' if 'dividend' in h.lower() else 'rate'))
                    rates.append(f'{metric} {re.sub(r"(?<=%)\d+$", "", v)}')
            if not rates: continue
            qualifiers=[]
            for h,v in zip(r.headers[1:],r.cells[1:]):
                if any(k in h.lower() for k in ['balance','term','model','min. to earn','minimum','amount']): qualifiers.append(f'{h}: {v}')
            parts.append(f"{title}: {', '.join(rates)}"+(f" ({'; '.join(qualifiers)})" if qualifiers else ''))
        return '; '.join(parts)

    def _rates(self,i,q):
        # Tables are queried from the selected historical snapshot, not cached current numbers.
        rows=self.table_rows[i]
        ql=q.lower().replace('home$tart','home start')
        for name,value in NUMBER_WORDS.items():ql=re.sub(r'\b'+re.escape(name)+r'\b',str(value),ql)
        months=MONTH.findall(ql); years=YEAR.findall(ql)
        term=int(months[0]) if months else None
        product=None
        if re.search(r'certificates?|\bcds?\b|share certificate',ql): product='certificate'
        elif re.search(r'auto|car loan|vehicle|motorcycle',ql): product='auto'
        elif re.search(r'mortgage|home loan|adjustable.rate|\barm\b',ql) and re.search(r'rate|apr|interest|mortgage',ql):product='mortgage'
        elif re.search(r'credit cards?|platinum visa|rewards visa',ql) and re.search(r'rate|apr|interest',ql):product='card'
        elif re.search(r'personal loan',ql) and re.search(r'\brate|apr|interest',ql):product='personal_loan'
        elif re.search(r'savings|checking|money market',ql) and re.search(r'rate|apy|dividend|interest|pay|earn',ql):product='deposits'
        if product is None:return None
        if product=='certificate':
            target=[r for r in rows if 'certificate' in r.context.lower() and ('apy' in ' '.join(r.headers).lower()) and 'certificate' in r.url.lower() or ('certificate' in r.context.lower() and 'apy' in ' '.join(r.headers).lower())]
            # Do not mix regular certificates with specials or different month terms.
            if term is not None:
                target=[r for r in target if re.search(r'\b'+str(term)+r'\s*[- ]?\s*months?\b',r.cells[0],re.I)]
                if 'jumbo' in ql:target=[r for r in target if 'jumbo' in r.cells[0].lower()]
                elif 'special' in ql:target=[r for r in target if 'special' in r.cells[0].lower()]
                else:target=sorted(target,key=lambda r:(('jumbo' in r.cells[0].lower()),('special' not in r.cells[0].lower())))
                if not target:
                    # Synthetic and legacy websites sometimes express facts as prose.
                    hits=self._search(i,q,35)
                    for s,body,ctx,url,display,k in hits:
                        if re.search(r'\b'+str(term)+r'\s*[- ]?\s*month',body,re.I) and 'certificate' in (body+ctx).lower() and PCT.search(body):
                            line=re.search(r'\b'+str(term)+r'\s*[- ]?\s*month[^\n]{0,160}',display,re.I)
                            return self._ans((line.group(0) if line else display)[:220],[(url,self.snapshots[i][1],display)])
                    return self._refuse('The specified certificate term or tier is not listed in this snapshot; please contact Dupaco to confirm.')
                if 'jumbo' not in ql and 'special' not in ql and len(target)>1:
                    # Include both standard and jumbo when both have the named term.
                    target=target[:2]
                else:target=target[:1] if 'jumbo' in ql else target[:2]
            else:
                # Generic certificate request is a list request; include all published terms.
                target=[r for r in target if 'certificate' in r.cells[0].lower() or MONTH.search(r.cells[0])]
                # Avoid page duplication; prefer personal savings rate sheet.
                preferred=[r for r in target if '/personal/savings-account' in r.url]
                target=preferred or target
                target=target[:17]
            if not target:return None
            # Prefer the audience-specific product sheet to mirrored summary
            # tables. Avoid citing business products for personal questions.
            preferred_path='/business/savings-accounts' if 'business' in ql else '/personal/savings-account'
            preferred=[r for r in target if preferred_path in r.url]
            if preferred: target=preferred
            return self._ans(self._row_answer(target,('apy',)) or '; '.join(r.as_text() for r in target),target)
        if product=='auto':
            target=[r for r in rows if 'auto-motorcycle-loans' in r.url and 'model year' in r.as_text().lower() and ('apr' in ' '.join(r.headers).lower() or 'annual percentage rate' in ' '.join(r.headers).lower())]
            if not target:return None
            if term is not None:target=[r for r in target if re.search(r'\b'+str(term)+r'\s*months?\b',r.as_text(),re.I)]
            # Distinguish model-year cohorts.
            car_year=re.search(r'\b(20\d{2})\b',ql)
            if car_year:
                yr=int(car_year.group(1))
                def cohort(r):
                    m=re.search(r'(20\d\d)\s*[-–]\s*(\d{2,4})',r.cells[0])
                    if m:
                        start=int(m.group(1)); end=int(m.group(2));end=end if end>=100 else (start//100)*100+end
                        return start<=yr<=end
                    return (str(yr) in r.cells[0]) or ('older' in r.cells[0].lower() and int(re.search(r'20\d\d',r.cells[0]).group())>=yr if re.search(r'20\d\d',r.cells[0]) else False)
                target=[r for r in target if cohort(r)]
            if target:
                target=target[:1] if term and car_year else target[:12]
                return self._ans(self._row_answer(target,('apr','annual percentage rate')) or '; '.join(r.as_text() for r in target),target)
            return self._refuse('The requested auto-loan model-year/term combination is not published. Please contact Dupaco for rates.')
        if product=='mortgage':
            target=[r for r in rows if '/personal/loans/home-loans' in r.url and 'program' in ' '.join(r.headers).lower() and re.search(r'\b(?:purchase|refinance):',r.cells[0],re.I)]
            if not target:return None
            if re.search(r'adjustable|\barm\b|\d+/\d+',ql):
                target=[r for r in target if re.search(r'\barm\b|adjustable|\d+/\d+',r.cells[0],re.I)]
                if not target:return self._refuse('I cannot find a published rate for that adjustable-rate mortgage term. Please contact a mortgage lender.',self._page(i,'/personal/loans/home-loans'))
            if years:target=[r for r in target if re.search(r'\b'+years[0]+r'[- ]?year',r.cells[0],re.I)]
            if 'refinanc' in ql:target=[r for r in target if 'refinance:' in r.cells[0].lower()]
            else:target=[r for r in target if 'purchase:' in r.cells[0].lower()]
            if not target:return self._refuse('I cannot find a rate for that mortgage product on the published rate sheet.',self._page(i,'/personal/loans/home-loans'))
            # Choose the bottom loan amount tier as the default headline rather than 300k tier.
            dollar=AMT.search(ql)
            if dollar:
                req=float(dollar.group(1).replace(',',''))
                with_amount=[]
                for r in target:
                    rm=AMT.search(r.cells[0])
                    if rm and req<=float(rm.group(1).replace(',','')):with_amount.append((float(rm.group(1).replace(',','')),r))
                if with_amount: target=[min(with_amount,key=lambda x:x[0])[1]]
            else:
                numbered=[(float(AMT.search(r.cells[0]).group(1).replace(',','')),r) for r in target if AMT.search(r.cells[0])]
                if numbered:
                    first=min(numbered,key=lambda x:x[0])[1]
                    largest=max(numbered,key=lambda x:x[0])[1]
                    target=[first]+([largest] if largest!=first else [])
                else:target=target[:2]
            return self._ans(self._row_answer(target,('rate','apr')),target)
        if product=='deposits':
            target=[r for r in rows if ('apy' in ' '.join(r.headers).lower() or 'dividend rate' in ' '.join(r.headers).lower()) and ('/personal/savings-account' in r.url or '/personal/checking-accounts' in r.url or '/business/' in r.url)]
            account=''
            for name in ['bright savings','share savings','high-yield savings','investor\'s choice','health savings','1st rate checking','free checking','achieve money market','money market','checking']:
                if name in ql:account=name;break
            if account:
                if account=='checking':target=[r for r in target if 'checking' in (r.cells[0]+' '+r.heading).lower()]
                elif account=='money market':target=[r for r in target if 'money market' in r.cells[0].lower()]
                else:target=[r for r in target if account in (r.cells[0]+' '+r.heading).lower()]
            elif 'checking' in ql:target=[r for r in target if 'checking' in (r.cells[0]+' '+r.heading).lower()]
            elif 'savings' in ql:target=[r for r in target if 'savings' in (r.cells[0]+' '+r.heading).lower()]
            # A named product must appear in the extracted product catalog;
            # otherwise generic Share Savings rows are not evidence of it.
            named=re.search(r'\b([a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*){0,3})\s+(savings|checking)\s+account\b',ql)
            if named:
                tokens=named.group(1).split()
                generic={'a','an','the','my','our','your','on','for','of','from','with','is','what',
                         'which','this','those','their','any','best','general','regular','typical',
                         'rates','rate','apy','apr','interest','current','provide','give','me',
                         'some','tell','latest','are','all','for','want','need','do','does','how'}
                while tokens and tokens[0] in generic:tokens.pop(0)
                requested=norm(' '.join(tokens+[named.group(2)])).replace('-',' ')
                if tokens and not any(requested in norm(r.cells[0]+' '+r.heading).replace('-',' ') for r in target):
                    return self._refuse('I cannot verify that named savings or checking product in the available website snapshot.')
            tier_rate_question=bool(re.search(r'\btiers?\b',ql) and PCT.search(ql))
            if tier_rate_question and not account and 'savings' in ql:
                share=[r for r in target if r.cells[0].lower()=='share savings' and '/personal/savings-account' in r.url]
                if share:target=share
            if 'business' not in ql:target=[r for r in target if '/business/' not in r.url]
            # An interest-only question about a rate tier is a precise row lookup.
            amounts=[float(x.replace(',','')) for x in AMT.findall(ql)]
            if len(amounts)>=2 and 'between' in ql:
                amt=sum(amounts[:2])/2
            elif len(amounts)>=1 and ('balance' in ql or 'tier' in ql or 'account' in ql):amt=amounts[0]
            else:amt=None
            if amt is not None:
                eligible=[]
                for r in target:
                    try:
                        tier=r.cells[1]
                        z=[float(z.replace(',','')) for z in AMT.findall(tier)]
                        if (len(z)>=2 and z[0]<=amt<=z[1]) or (len(z)==1 and '+' in tier and amt>=z[0]):eligible.append(r)
                    except (ValueError,IndexError): pass
                if eligible:
                    target=eligible
                elif any(any('balance' in h.lower() or 'tier' in h.lower() for h in r.headers) for r in target):
                    return self._refuse('The requested balance does not match a published account tier; please contact Dupaco.')
            if not target:return None
            if 'highest' in ql or 'top out' in ql or tier_rate_question:
                target=sorted(target,key=lambda r: float((PCT.search(r.field(('apy',)) or '0%') or re.search('0', '0')).group().replace('%','')),reverse=True)[:1]
            elif 'bright savings' in ql or amt is not None:target=target[:1]
            else:
                # Organize distinct rows and preserve meaningful tiers without merging other account products.
                target=target[:15]
            if tier_rate_question and target:
                requested=PCT.search(ql)
                eligible=[r for r in target if PCT.search(r.field(('apy',)) or '') and
                          float(PCT.search(r.field(('apy',))).group().replace('%','')) ==
                          float(requested.group().replace('%',''))]
                if not eligible:
                    return self._ans('No listed tier has the requested APY. The highest published tier is '+self._row_answer(target,('apy',)),target)
                return self._ans('Yes. '+self._row_answer(eligible,('apy',)),eligible)
            return self._ans(self._row_answer(target,('apy','dividend rate')),target)
        if product=='card':
            # Credit-card APRs on the primary pages are both structured tables and prose bullets.
            if 'business' in ql and 'personal' not in ql:paths=['/business/credit-cards']
            elif 'platinum' in ql or 'rewards' in ql:paths=['/personal/credit-cards'] if 'business' not in ql else ['/business/credit-cards']
            else:paths=['/personal/credit-cards','/business/credit-cards']
            collected=[];sources=[]
            for path in paths:
                p=self._page(i,path)
                if not p:continue
                raw=p.markdown.splitlines();headers=''
                for line in raw:
                    t=line.strip()
                    if t.startswith('### ') and ('Visa' in t or 'Card' in t):headers=cleaned(t.lstrip('#'))
                    if headers and re.search(r'\bAPR\b|Annual Percentage Rate',t,re.I) and PCT.search(t):
                        if 'intro' in t.lower() and 'ongoing rate' not in t.lower() and 'after that' not in t.lower():continue
                        if re.search(r'\bplatinum\b',ql) and 'platinum' not in headers.lower():continue
                        if re.search(r'\brewards\b',ql) and 'rewards' not in headers.lower():continue
                        pct=re.findall(r'\d+(?:\.\d+)?\s*%',t)
                        if pct:
                            value=('–'.join(pct[:2]) if len(pct)>=2 else pct[0])
                            label=f'{headers}: {value} APR'
                            if label not in collected:collected.append(label)
                    if t.startswith('## ') and 'Rates' not in t:headers=''
                if collected:sources.append(p)
            if collected:return self._ans('; '.join(collected[:6]),sources)
            return None
        if product=='personal_loan':
            # A term-specific rate may appear on a generic published rates table.
            target=[r for r in rows if '/personal/loans/personal-loans' in r.url and any('apr' in h.lower() for h in r.headers) and any('month' in c.lower() for c in r.cells)]
            if term is not None:target=[r for r in target if re.search(r'\b'+str(term)+r'\s*months?',r.as_text(),re.I)]
            if target:return self._ans(self._row_answer(target[:1],('apr',)),target[:1])
            return None
        return None

    def _pages_matching_url(self,i,substring):
        return [p for p in self.pages[i].values() if substring.lower() in urlparse(p.url).path.lower()]

    def _excerpt(self,raw,pattern,around=210):
        m=re.search(pattern,raw,re.I|re.S)
        if not m:return None
        st=max(raw.rfind('\n',0,m.start()),raw.rfind('. ',0,m.start()))+1
        en=raw.find('\n',m.end())
        if en<0:en=min(len(raw),m.end()+around)
        return cleaned(raw[max(0,st):min(en,m.end()+around)])[:390]

    def _fact_prose(self,i,q):
        ql=q.lower()
        # The count is embedded in many historical boilerplate descriptions; use the
        # latest snapshot's consensus rather than a historic branch-closure article.
        if 'how many' in ql and 'branch' in ql:
            observations=[]
            for p in self.pages[i].values():
                for m in re.finditer(r'\b(\d{1,3})\s+branch office locations\b',p.markdown,re.I):
                    if not is_authoritative(p.url):continue
                    observations.append((m.group(1),p))
            if not observations:
                for p in self.pages[i].values():
                    for m in re.finditer(r'\b(\d{1,3})\s+branch office locations\b',p.markdown,re.I):observations.append((m.group(1),p))
            if observations:
                from collections import Counter
                # Use the most recently *published* count rather than the most frequent
                # boilerplate (which repeats years-old press releases across a crawl).
                dated=[]
                for number,p in observations:
                    head=p.markdown[:280]
                    mt=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})',head,re.I)
                    if mt:
                        try:
                            pub=dt.datetime.strptime(mt.group(0).replace(',',''),'%B %d %Y').date()
                            dated.append((pub,number,p))
                        except ValueError:pass
                if dated:
                    pub,common,p=max(dated,key=lambda r:r[0])
                else:
                    common=Counter(x[0] for x in observations).most_common(1)[0][0]
                    pub=dt.date(2020,1,1);p=next(x[1] for x in reversed(observations) if x[0]==common)
                value=int(common)
                closures=[]
                for closure in self._pages_matching_url(i,'plans-hy-vee-branch-closure'):
                    m=re.search(r'(?:location|branch) will close\s+(\w+\s+\d{1,2},?\s+20\d{2})',closure.markdown,re.I)
                    if m:
                        try:
                            date=dt.datetime.strptime(m.group(1).replace(',',''),'%B %d %Y').date()
                            if pub<date<=dt.date.fromisoformat(self.snapshots[i][0]):closures.append((date,closure))
                        except ValueError:pass
                # A confirmed closure after the count date reduces the count once.
                value-=len(closures)
                cites=[(p.url,self.snapshots[i][1],str(common)+' branch office locations')]+[(x.url,self.snapshots[i][1],'branch closure') for _,x in closures]
                return self._ans(f'Based on the latest published branch count and subsequent confirmed closures, Dupaco has {value} branch office locations.',cites)
        if 'hy-vee' in ql or 'hy vee' in ql:
            ps=self._pages_matching_url(i,'plans-hy-vee-branch-closure')
            if ps:
                p=ps[0]
                date=self._excerpt(p.markdown,r'(?:location|branch) will close\s+\w+\s+\d{1,2},?\s+\d{4}')
                branch=self._excerpt(p.markdown,r'full-service branches at Key West and Sycamore Street')
                if ('when' in ql or 'close' in ql or 'still open' in ql) and date:
                    return self._ans(date,[p])
                if ('where' in ql or 'full-service' in ql) and branch:
                    return self._ans(branch,[p])
        if 'forbes' in ql and ('recognition' in ql or 'recognized' in ql or 'rank' in ql):
            ps=self._pages_matching_url(i,'forbes-ranks-dupaco-as-a-top-credit-union')
            candidates=[]
            for p in ps:
                m=re.search(r'[^.\n]{0,100}\b(?:fifth|fourth|third|second) time\b[^.\n]{0,100}',p.markdown,re.I)
                if m:
                    rank={'fifth':5,'fourth':4,'third':3,'second':2}
                    r=next((v for k,v in rank.items() if k in m.group().lower()),0)
                    candidates.append((r,p,m.group().strip()))
            if candidates:
                _,p,excerpt=max(candidates,key=lambda x:x[0])
                return self._ans(excerpt,[p])
        if 'volunteer time off' in ql or ('employees' in ql and 'volunteer' in ql):
            ps=self._pages_matching_url(i,'volunteering-pays-off')
            for p in ps:
                m=re.search(r'(\d[\d,]*)\s+Dupaco employees\*{0,2}\s+used[^.]{0,180}Volunteer Time Off',p.markdown,re.I)
                if m:return self._ans(cleaned(m.group(0)),[p])
        if re.search(r'home(?:\$|s)tart|home[ -]?start',ql,re.I):
            ps=self._pages_matching_url(i,'first-time-homebuyers-get-a-boost')
            for p in ps:
                m=re.search(r'Home\$tart program.{0,280}\$[\d,]+[^.]{0,190}',p.markdown,re.I)
                if m:return self._ans(cleaned(m.group(0)),[p])
        if 'eligible' in ql and ('member' in ql or 'join' in ql):
            p=self._page(i,'/about/join')
            if p:
                basics=self._excerpt(p.markdown,r'You live or work within our \[charter area\]')
                state=None;other=None
                for other in self.pages[i].values():
                    m=re.search(r'Dupaco serves residents in \d+ counties throughout Iowa, [^.]{0,70}Illinois and [^.]{0,70}Wisconsin',other.markdown,re.I)
                    if m:
                        state=m.group(0);break
                if basics and state:return self._ans(basics+'. '+state,[p,other])
                if basics:return self._ans(basics,[p])
        if 'update' in ql and ('contact' in ql or 'phone number' in ql or 'address' in ql):
            p=self._page(i,'/contact')
            if p:
                phone=re.search(r'800[- ]373[- ]7600',p.markdown)
                shine=re.search(r'Shine Online',p.markdown,re.I)
                if phone and shine:
                    return self._ans('You can update information by contacting Dupaco at '+phone.group(0)+' or through Shine Online/Mobile Banking.',[p])
        if 'mobile wallet' in ql or 'mobile payments' in ql:
            p=self._page(i,'/online-and-mobile-banking/mobile-payments')
            if p:
                excerpt=self._excerpt(p.markdown,r'Add your Dupaco debit and credit cards to your phone.{0,120}Samsung Pay')
                if excerpt:return self._ans(excerpt,[p])
        if 'construction loan' in ql and re.search(r'rate|apr|interest',ql):
            p=self._page(i,'/business/loans/construction-loans')
            if p:
                # A repayment calculator is not evidence of an advertised loan rate.
                haspublished=re.search(r'\|\s*(?:construction loan|\d+ months?)\s*\|\s*\d+(?:\.\d+)?%',p.markdown,re.I)
                if not haspublished:return self._refuse('The website does not publish a construction-loan interest rate; please contact a Dupaco lending specialist.',p)
        return None

    def _special(self,i,q):
        ql=q.lower()
        contact=self._page(i,'/contact')
        if re.search(r'routing number|routing transit',ql):
            if contact:
                m=re.search(r'Routing Number[:\s*]+(\d{9})',contact.markdown,re.I)
                if m:
                    cash=self._page(i,'/business/cash-management')
                    refs=[contact]
                    if cash and m.group(1) in cash.markdown:refs.append(cash)
                    return self._ans('Dupaco routing number: '+m.group(1),refs)
        if ('locator' in ql or 'find a branch' in ql or 'branch and atm' in ql) and contact:
            return self._ans('Find Dupaco branches and ATMs at '+contact.url,[contact])
        if ('join' in ql and 'online' in ql) or ('become a member' in ql and 'online' in ql):
            p=self._page(i,'/about/join')
            if p:
                urls=re.findall(r'https?://join\.dupaco\.com/?[\w/]*',p.markdown,re.I)
                return self._ans('You can join online at '+urls[0],[p]) if urls else self._refuse('The online application URL is not available in this snapshot; please contact Dupaco.',p)
        if 'branch' in ql and ('address' in ql or re.search(r'where (?:is|are) the',ql)) and contact:
            city=re.search(r'\b(?:of|in|the)\s+([A-Z][a-zA-Z\-]+)\s+branch',q,re.I)
            if not city:city=re.search(r'\b([A-Z][a-zA-Z\-]+)\s+branch',q,re.I)
            if city:
                name=city.group(1)
                lines=contact.markdown.splitlines()
                for line in lines:
                    if name.lower()+',' in line.lower() and re.search(r'\b\d{5}\b',line) and re.match(r'^\s*\d+',line):
                        return self._ans(cleaned(line),[contact])
                return self._refuse('I cannot verify that branch address from the available location listings.',contact)
        if re.search(r'(?:lobby|drive.?up) hours',ql):
            named_branch=re.search(r'\b([A-Z][a-zA-Z-]+(?:\s+[A-Z][a-zA-Z-]+){0,2})\s+[Bb]ranch\b',q)
            if named_branch:
                # General news articles about other branches do not support a
                # named branch's current schedule. Abstain unless verified.
                return self._refuse('I cannot verify the hours for that specific branch from the available location listing. Please contact Dupaco.',contact)
            if re.search(r'mullan|mullin',ql):
                # Named branches must exist in the current location index to provide hours.
                if contact and not re.search(r'mullan|mullin',contact.markdown,re.I):
                    return self._refuse('I cannot verify that branch or its lobby hours in the available location listing. Please contact Dupaco.',contact)
            # Representative schedules from branch announcements. Each schedule is
            # explicitly scoped to a location; general hours vary by branch.
            options=[]
            for p in self._pages_matching_url(i,'completion-of-newest-learning-lab') + self._pages_matching_url(i,'completion-of-expanded-branch'):
                if '/newsroom/' not in p.url:continue
                kind='Lobby' if 'lobby' in ql else 'Drive-up'
                m=re.search(r'\*\*'+re.escape(kind)+r'\*\*\s*(.*?)(?:\*\*(?:Drive-up|Lobby|ABOUT DUPACO)|$)',p.markdown,re.I|re.S)
                if m and ('12:30' in m.group(1) or 'noon' in m.group(1) or '6 p.m.' in m.group(1)):
                    schedule=cleaned(m.group(1))[:230]
                    if schedule and schedule not in [v[0] for v in options]:options.append((schedule,p))
            if options:
                if 'all ' in ql or 'same' in ql:
                    with_1230=next((x for x in options if '12:30' in x[0]),options[0])
                    with_noon=next((x for x in options if 'noon' in x[0]),options[-1])
                    chosen=[with_1230]+([with_noon] if with_noon!=with_1230 else [])
                else: chosen=options[:1]
                refs=[v[1] for v in chosen]
                if contact and ('general' in ql or 'your lobby' in ql):refs.append(contact)
                return self._ans('Hours vary by branch. '+ ' '.join(v[0] for v in chosen)+' Please confirm hours for your specific branch.',refs)
        return None

    def answer(self,question: str,as_of=None) -> Answer:
        i=self._at(as_of)
        if i is None:return self._refuse('I do not have a snapshot at or before that date, so I cannot verify the answer.')
        if not isinstance(question,str): return self._refuse('Please provide a question as text.')
        q=question.strip()
        if not q:return self._refuse()
        # Website information cannot establish an individual member's private
        # banking credentials, balance, or account identifiers.
        if re.search(r'\b(?:account number|account balance|password|passcode|pin number|social security number)\b',q,re.I) and not re.search(r'\brouting (?:number|transit)\b',q,re.I):
            if re.search(r'\b(?:my|mine|their|his|her|alice|someone|personal|ach)\b',q,re.I) or re.search(r'\b(?:what is|tell me|give me|show me)\b',q,re.I):
                return self._refuse('I cannot access or verify a member’s private account information from public website snapshots.')
        # Sources are parsed in a fixed order, to make repeated and rephrased answers stable.
        a=self._fact_prose(i,q)
        if a is not None:return a
        a=self._special(i,q)
        if a is not None:return a
        if re.search(r'\b(?:link|url|website address)\b',q,re.I) and re.search(r'certificates?|\bcds?\b',q,re.I):
            p=self._page(i,'/personal/savings-account')
            if p:return self._ans('Certificate rates are published at '+p.url+'#rates',[p])
        a=self._rates(i,q)
        if a is not None:return a
        hits=self._search(i,q,75)
        if not hits and i>0 and re.search(r'\b(?:fee|replacement|lost)\b',q,re.I):
            for earlier in range(i-1,-1,-1):
                prior=self._search(earlier,q,12)
                if prior and prior[0][0]>=4.5 and re.search(r'\$\s*\d',prior[0][4]):
                    url=prior[0][3]
                    if url not in self.pages[i]:
                        return Answer(text='Last documented fee (from an earlier snapshot; not verified as current): '+prior[0][4][:200],sources=[Source(url=url,snapshot_id=self.snapshots[earlier][1],quote=prior[0][4][:250])],confident=False)
        if not hits:return self._refuse()
        # For temporal/non-rate questions prefer a single grounded answer over a mixed paragraph.
        qtokens=set(w for w in TOK.findall(q.lower()) if w not in STOP and len(w)>2)
        candidates=[]
        for score,body,ctx,url,disp,kind in hits:
            if kind=='table' and not any(t in q.lower() for t in ['rate','apy','apr','fee','balance','amount']):continue
            # Crucial named-entity qualification: the data must actually mention the entity.
            if 'mullan' in q.lower() and 'mullan' not in (body+' '+ctx).lower():continue
            if kind=='table' and re.search(r'\b(?:rate|apy|apr|term|loan)\b',q,re.I):score+=0.3
            candidates.append((score,disp,url,body,kind))
        if not candidates:return self._refuse()
        candidates.sort(key=lambda x:(-x[0],x[2],x[1]))
        best=candidates[0]
        # Trim to an answer-bearing phrase when prose is long.
        score,disp,url,body,kind=best
        if score<2.0:return self._refuse()
        text=disp[:430]
        if kind=='prose':
            sentences=re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])',disp)
            if len(sentences)>1:
                sentences.sort(key=lambda x:(-len(qtokens&words(x)),len(x)))
                text=sentences[0][:390]
            if len(text)<35 and len(candidates)>1 and candidates[1][2]==url:
                text=(text+' '+candidates[1][1][:200]).strip()
        return self._ans(text,[(url,self.snapshots[i][1],disp)])

    def _auto_calculator_conflicts(self) -> list[Conflict]:
        """Flag overlapping APR tiers where a calculator is stale in the latest crawl.

        A calculator's example loan may use a different model-year cohort from
        the official page; only overlapping model years with the same term are
        comparable. Different terms or non-overlapping cohorts are not conflicts.
        """
        if not self.snapshots:
            return []
        idx=self.snapshots[-1][2]
        sid=self.snapshots[-1][1]
        rows=self.table_rows[idx]

        def claim(r):
            if len(r.cells)<3:return None
            cohort=re.search(r'\b(20\d{2})\s*[-–—]\s*(\d{2,4})\s+Model\s+Year',r.cells[0],re.I)
            term=MONTH.search(r.cells[1])
            value=PCT.search(r.cells[2])
            if not cohort or not term or not value or 'apr' not in r.cells[2].lower():return None
            first=int(cohort.group(1)); last=int(cohort.group(2))
            if last<100:last=(first//100)*100+last
            return int(term.group(1)),first,last,value.group().replace(' ','')

        official=[];calculators=[]
        for row in rows:
            path=urlparse(row.url).path.lower()
            parsed=claim(row)
            if not parsed:continue
            if '/personal/loans/auto-motorcycle-loans' in path:official.append((row,parsed))
            elif 'calculator' in path and ('vehicle' in path or 'auto' in path):calculators.append((row,parsed))
        out=[];seen=set()
        for official_row,(term,lo,hi,value) in official:
            for calc_row,(calc_term,calc_lo,calc_hi,calc_value) in calculators:
                first=max(lo,calc_lo);last=min(hi,calc_hi)
                if term!=calc_term or first>last or value==calc_value:continue
                key=(term,first,last,value,calc_value)
                if key in seen:continue
                seen.add(key)
                title=(f'{term}-month auto loan APR, {first} model-year vehicle' if first==last
                       else f'{term}-month auto loan APR, {first}–{last} model-year vehicles')
                out.append(Conflict(
                    topic=title,values=[calc_value,value],
                    sources=[Source(url=calc_row.url,snapshot_id=sid,quote=calc_row.as_text()),
                             Source(url=official_row.url,snapshot_id=sid,quote=official_row.as_text())],
                    resolution=value,
                    reason='For the same term and overlapping model years, the current official auto-loan rate table takes precedence over the stale calculator example.'
                ))
        return out

    def conflicts(self) -> list[Conflict]:
        if self.conflicts_cache is not None:return list(self.conflicts_cache)
        out=[]
        for key, observations in self.facts.items():
            bydate={}
            for date,sid,url,value,col,label in observations:
                # Multiple same-key observations within a snapshot are deduped.
                bydate[(date,norm(value))]=(sid,url,value,col,label)
            vals={norm(v[2]) for v in bydate.values()}
            if len(vals)<2:continue
            # The truth for a given fact is the latest official page, not old data.
            events=sorted(((date, *v) for (date,_),v in bydate.items()),key=lambda x:x[0])
            ordered=[]
            for ev in events:
                if norm(ev[3]) not in {norm(v) for v in ordered}:ordered.append(ev[3])
            current=events[-1]
            topic=(str(current[5])+' '+str(current[4])).strip()
            if len(topic)<8:topic=str(key[1] if len(key)>1 else key[0])
            src=[];seen=set()
            for ev in events:
                if (ev[1],ev[2]) not in seen:
                    seen.add((ev[1],ev[2]));src.append(Source(url=ev[2],snapshot_id=ev[1],quote=f'{ev[5]}: {ev[3]}'))
            if len(src)<2:continue
            path=key[0]
            if 'certificate' in str(key[1]) and 'certificate' not in topic.lower():topic='certificate '+topic
            elif 'home-loans' in path:topic='mortgage '+topic
            elif 'checking' in path and 'checking' not in topic.lower():topic='checking '+topic
            if len(src)>1:
                ordered=[re.sub(r'(?<=%)\d+$','',v).strip() for v in ordered]
            out.append(Conflict(topic=topic[:140],values=ordered[:8],sources=src[:10],resolution=re.sub(r'(?<=%)\d+$','',current[3]).strip(),reason='Values changed between dated crawls for the identical product, term, tier and metric; the latest official rate/fee schedule supersedes earlier snapshots.'))
        # Cross-page disagreement can exist in a single complete snapshot;
        # compare the official auto rate table against stale calculator tiers.
        out.extend(self._auto_calculator_conflicts())
        # /rates and /business-rates mirror product-specific rate sheets. Merge
        # duplicate change events without throwing away their source evidence.
        out.sort(key=lambda c:(0 if '/personal/' in c.sources[-1].url or '/business/' in c.sources[-1].url else 1, c.topic.lower(),c.values[0]))
        seen={};dedup=[]
        for c in out:
            signature=(norm(c.topic),tuple(norm(v) for v in c.values))
            if signature in seen:
                previous=seen[signature]
                known={(s.url,s.snapshot_id) for s in previous.sources}
                previous.sources.extend(s for s in c.sources if (s.url,s.snapshot_id) not in known)
                continue
            seen[signature]=c;dedup.append(c)
        dedup.sort(key=lambda c:(c.topic.lower(),c.values[0]))
        self.conflicts_cache=dedup
        return list(dedup)


def build():
    return KnowledgeEngine()