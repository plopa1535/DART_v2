"""
DART Equity vs Rates Duration Dashboard
Flask Backend Application

주요 생명보험사(교보생명, 삼성생명, 한화생명)의 자본총계와 시장 금리를 분석하여
금리 민감도(듀레이션)를 산출하는 웹 애플리케이션
"""

import os
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from statistics import median

import requests
import pandas as pd
import numpy as np
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from dotenv import load_dotenv
from cachetools import TTLCache

# 환경변수 로드
load_dotenv()

app = Flask(__name__)
CORS(app)

# ============================================================================
# API 키 설정
# ============================================================================
DART_API_KEY = os.getenv('DART_API_KEY', '')
ECOS_API_KEY = os.getenv('ECOS_API_KEY', '')
FRED_API_KEY = os.getenv('FRED_API_KEY', '')

# ============================================================================
# 캐시 설정 (TTL: 6시간)
# ============================================================================
dart_cache = TTLCache(maxsize=256, ttl=21600)
ecos_cache = TTLCache(maxsize=256, ttl=21600)
fred_cache = TTLCache(maxsize=256, ttl=21600)
corp_code_cache = TTLCache(maxsize=10, ttl=86400)  # 24시간

# ============================================================================
# 회사 매핑 (corp_code 하드코딩 - DART에서 조회한 값)
# ============================================================================
COMPANY_MAP = {
    'samsung': {'name': '삼성생명', 'corp_code': '00126256'},  # 삼성생명보험주식회사
    'hanwha': {'name': '한화생명', 'corp_code': '00113058'},   # 한화생명보험주식회사
    'kyobo': {'name': '교보생명', 'corp_code': '00112882'},    # 교보생명보험주식회사
    'shinhan': {'name': '신한라이프', 'corp_code': '00137517'} # 신한라이프생명보험주식회사
}

# ECOS 국고채 10년 시계열 코드 (탐색 후 캐싱)
ECOS_KR10Y_CODE = None


def get_corp_codes():
    """DART에서 corp_code ZIP 다운로드 후 3사 매핑"""
    cache_key = 'corp_codes'
    if cache_key in corp_code_cache:
        return corp_code_cache[cache_key]

    if not DART_API_KEY:
        raise ValueError("DART_API_KEY가 설정되지 않았습니다.")

    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"

    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()

        # ZIP 파일 해제
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            xml_filename = z.namelist()[0]
            with z.open(xml_filename) as f:
                tree = ET.parse(f)
                root = tree.getroot()

        corp_codes = {}

        for company_id, info in COMPANY_MAP.items():
            search_name = info['search_name']
            found = False

            for corp in root.findall('.//list'):
                corp_name = corp.find('corp_name').text if corp.find('corp_name') is not None else ''
                corp_code = corp.find('corp_code').text if corp.find('corp_code') is not None else ''

                # 완전 일치 또는 포함 매칭
                if search_name in corp_name or corp_name in search_name:
                    corp_codes[company_id] = corp_code
                    found = True
                    break

            if not found:
                # 공백/특수문자 제거 후 재시도
                clean_search = search_name.replace(' ', '').replace('(주)', '').replace('주식회사', '')
                for corp in root.findall('.//list'):
                    corp_name = corp.find('corp_name').text if corp.find('corp_name') is not None else ''
                    clean_name = corp_name.replace(' ', '').replace('(주)', '').replace('주식회사', '')

                    if clean_search in clean_name or clean_name in clean_search:
                        corp_codes[company_id] = corp.find('corp_code').text
                        break

        corp_code_cache[cache_key] = corp_codes
        return corp_codes

    except Exception as e:
        raise Exception(f"DART corp_code 조회 실패: {str(e)}")


def get_dart_equity(company_id: str, year_count: int = 3):
    """DART에서 별도 재무제표 기준 자본총계 조회"""
    cache_key = f"equity_{company_id}_{year_count}"
    if cache_key in dart_cache:
        return dart_cache[cache_key]

    if not DART_API_KEY:
        raise ValueError("DART_API_KEY가 설정되지 않았습니다.")

    # 하드코딩된 corp_code 사용
    corp_code = COMPANY_MAP[company_id]['corp_code']

    current_year = datetime.now().year

    # 분기 데이터 수집
    quarters_data = []

    for year in range(current_year - year_count, current_year + 1):
        # 보고서 유형별 조회 (1분기, 반기, 3분기, 사업)
        report_codes = [
            ('11013', f'{year}-03-31', '1Q'),  # 1분기보고서
            ('11012', f'{year}-06-30', '2Q'),  # 반기보고서
            ('11014', f'{year}-09-30', '3Q'),  # 3분기보고서
            ('11011', f'{year}-12-31', '4Q'),  # 사업보고서
        ]

        for reprt_code, quarter_end, quarter_name in report_codes:
            # 미래 분기는 건너뛰기
            quarter_date = datetime.strptime(quarter_end, '%Y-%m-%d')
            if quarter_date > datetime.now():
                continue

            try:
                # 단일회사 주요계정 조회 API (더 간단하고 안정적)
                url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
                params = {
                    'crtfc_key': DART_API_KEY,
                    'corp_code': corp_code,
                    'bsns_year': str(year),
                    'reprt_code': reprt_code
                }

                response = requests.get(url, params=params, timeout=30)
                data = response.json()

                if data.get('status') == '000' and data.get('list'):
                    quarter_item = {'quarter': quarter_end}

                    for item in data['list']:
                        account_nm = item.get('account_nm', '')
                        fs_div = item.get('fs_div', '')  # OFS: 별도, CFS: 연결

                        if fs_div != 'OFS':
                            continue

                        amount_str = item.get('thstrm_amount', '0')
                        if not amount_str or amount_str == '-':
                            continue

                        amount = int(amount_str.replace(',', ''))

                        # 자본총계
                        if '자본총계' in account_nm or account_nm == '자본 총계':
                            quarter_item['equity'] = amount
                        # 자산총계
                        elif '자산총계' in account_nm or account_nm == '자산 총계':
                            quarter_item['asset'] = amount
                        # 부채총계
                        elif '부채총계' in account_nm or account_nm == '부채 총계':
                            quarter_item['liability'] = amount

                    if 'equity' in quarter_item:
                        quarters_data.append(quarter_item)

            except Exception as e:
                print(f"DART 조회 오류 ({year} {quarter_name}): {e}")
                continue

    # 중복 제거 및 정렬
    df = pd.DataFrame(quarters_data)
    if df.empty:
        raise ValueError("자본총계 데이터를 찾을 수 없습니다.")

    df = df.drop_duplicates(subset=['quarter']).sort_values('quarter')
    df = df.tail(year_count * 4)  # 최근 N년치만

    result = df.to_dict('records')
    dart_cache[cache_key] = result
    return result


def search_ecos_kr10y_code():
    """ECOS API로 국고채 10년물 시계열 코드 탐색"""
    global ECOS_KR10Y_CODE

    if ECOS_KR10Y_CODE:
        return ECOS_KR10Y_CODE

    if 'ecos_kr10y' in ecos_cache:
        ECOS_KR10Y_CODE = ecos_cache['ecos_kr10y']
        return ECOS_KR10Y_CODE

    if not ECOS_API_KEY:
        raise ValueError("ECOS_API_KEY가 설정되지 않았습니다.")

    # 금리 관련 통계표 검색 - 시장금리(일별)
    # 통계표코드: 817Y002 (시장금리-일별)
    # 항목코드: 010200000 (국고채(10년))

    # 먼저 통계 목록에서 금리 관련 항목 탐색
    try:
        # 시장금리(일별) 통계표의 항목 목록 조회
        url = f"https://ecos.bok.or.kr/api/StatisticItemList/{ECOS_API_KEY}/json/kr/1/100/817Y002"
        response = requests.get(url, timeout=30)
        data = response.json()

        if 'StatisticItemList' in data and 'row' in data['StatisticItemList']:
            for item in data['StatisticItemList']['row']:
                item_name = item.get('ITEM_NAME', '')
                if '국고채' in item_name and '10년' in item_name:
                    ECOS_KR10Y_CODE = {
                        'stat_code': '817Y002',
                        'item_code': item.get('ITEM_CODE')
                    }
                    ecos_cache['ecos_kr10y'] = ECOS_KR10Y_CODE
                    return ECOS_KR10Y_CODE

        # 기본값 사용 (국고채 10년)
        ECOS_KR10Y_CODE = {
            'stat_code': '817Y002',
            'item_code': '010200000'
        }
        ecos_cache['ecos_kr10y'] = ECOS_KR10Y_CODE
        return ECOS_KR10Y_CODE

    except Exception as e:
        # 기본값으로 폴백
        ECOS_KR10Y_CODE = {
            'stat_code': '817Y002',
            'item_code': '010200000'
        }
        return ECOS_KR10Y_CODE


def get_kr10y_rate(quarter_dates: list):
    """ECOS에서 한국 국고채 10년물 금리 조회"""
    cache_key = f"kr10y_{'-'.join(quarter_dates[:3])}"
    if cache_key in ecos_cache:
        return ecos_cache[cache_key]

    if not ECOS_API_KEY:
        raise ValueError("ECOS_API_KEY가 설정되지 않았습니다.")

    code_info = search_ecos_kr10y_code()

    # 날짜 범위 계산
    start_date = min(quarter_dates).replace('-', '')
    end_date = max(quarter_dates).replace('-', '')

    # 여유 있게 시작일 조정 (직전 영업일 대비)
    start_dt = datetime.strptime(start_date, '%Y%m%d') - timedelta(days=10)
    start_date = start_dt.strftime('%Y%m%d')

    try:
        url = f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{code_info['stat_code']}/D/{start_date}/{end_date}/{code_info['item_code']}"
        response = requests.get(url, timeout=30)
        data = response.json()

        rates = {}
        if 'StatisticSearch' in data and 'row' in data['StatisticSearch']:
            for item in data['StatisticSearch']['row']:
                date_str = item.get('TIME', '')
                if len(date_str) == 8:
                    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    try:
                        rates[formatted_date] = float(item.get('DATA_VALUE', 0))
                    except:
                        pass

        # 분기말 금리 추출 (직전 영업일 값 사용)
        result = {}
        for q_date in quarter_dates:
            if q_date in rates:
                result[q_date] = rates[q_date]
            else:
                # 직전 영업일 찾기
                q_dt = datetime.strptime(q_date, '%Y-%m-%d')
                for i in range(1, 10):
                    prev_date = (q_dt - timedelta(days=i)).strftime('%Y-%m-%d')
                    if prev_date in rates:
                        result[q_date] = rates[prev_date]
                        break

        ecos_cache[cache_key] = result
        return result

    except Exception as e:
        raise Exception(f"ECOS 금리 조회 실패: {str(e)}")


def get_us10y_rate(quarter_dates: list):
    """FRED에서 미국 10년물 금리 조회"""
    cache_key = f"us10y_{'-'.join(quarter_dates[:3])}"
    if cache_key in fred_cache:
        return fred_cache[cache_key]

    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY가 설정되지 않았습니다.")

    # 날짜 범위 계산
    start_date = min(quarter_dates)
    end_date = max(quarter_dates)

    # 여유 있게 시작일 조정
    start_dt = datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=10)
    start_date = start_dt.strftime('%Y-%m-%d')

    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            'series_id': 'DGS10',
            'api_key': FRED_API_KEY,
            'file_type': 'json',
            'observation_start': start_date,
            'observation_end': end_date
        }

        response = requests.get(url, params=params, timeout=30)
        data = response.json()

        rates = {}
        if 'observations' in data:
            for obs in data['observations']:
                date_str = obs.get('date', '')
                value = obs.get('value', '.')
                if value != '.':
                    try:
                        rates[date_str] = float(value)
                    except:
                        pass

        # 분기말 금리 추출 (직전 영업일 값 사용)
        result = {}
        for q_date in quarter_dates:
            if q_date in rates:
                result[q_date] = rates[q_date]
            else:
                # 직전 영업일 찾기
                q_dt = datetime.strptime(q_date, '%Y-%m-%d')
                for i in range(1, 10):
                    prev_date = (q_dt - timedelta(days=i)).strftime('%Y-%m-%d')
                    if prev_date in rates:
                        result[q_date] = rates[prev_date]
                        break

        fred_cache[cache_key] = result
        return result

    except Exception as e:
        raise Exception(f"FRED 금리 조회 실패: {str(e)}")


def calculate_duration(equity_qoq: list, rate_change: list):
    """
    민감도 계산: D_t = equity_change / rate_change

    양수: 금리와 자본이 같은 방향으로 움직임
    음수: 금리와 자본이 반대 방향으로 움직임
    """
    duration_series = []
    valid_durations = []

    for i in range(len(equity_qoq)):
        if i == 0 or equity_qoq[i] is None or rate_change[i] is None:
            duration_series.append(None)
        elif rate_change[i] == 0:
            duration_series.append(None)
        else:
            d = equity_qoq[i] / rate_change[i]
            # 이상치 필터링 (절대값 100 초과는 제외)
            if abs(d) <= 100:
                duration_series.append(round(d, 2))
                valid_durations.append(d)
            else:
                duration_series.append(None)

    # Summary는 median 사용 (강건)
    summary = round(median(valid_durations), 2) if valid_durations else None

    return duration_series, summary


# ============================================================================
# API Endpoints
# ============================================================================

@app.route('/')
def index():
    """메인 페이지"""
    return render_template('index.html')


@app.route('/api/companies', methods=['GET'])
def get_companies():
    """분석 대상 회사 목록 반환"""
    companies = [
        {"id": "samsung", "name": "삼성생명"},
        {"id": "hanwha", "name": "한화생명"},
        {"id": "kyobo", "name": "교보생명"},
        {"id": "shinhan", "name": "신한라이프"}
    ]
    return jsonify(companies)


@app.route('/api/analyze', methods=['POST'])
def analyze():
    """자본-금리 민감도 분석 실행"""
    try:
        data = request.get_json() or {}
        company_id = data.get('company_id', 'samsung')
        year_count = data.get('year_count', 3)

        if company_id not in COMPANY_MAP:
            return jsonify({
                "error": {
                    "source": "INPUT",
                    "message": f"지원하지 않는 회사입니다: {company_id}",
                    "detail": f"지원 회사: {list(COMPANY_MAP.keys())}"
                }
            }), 400

        # 1. 자본총계 데이터 조회
        try:
            equity_data = get_dart_equity(company_id, year_count)
        except Exception as e:
            return jsonify({
                "error": {
                    "source": "DART",
                    "message": "자본총계 데이터 조회 실패",
                    "detail": str(e)
                }
            }), 500

        quarters = [item['quarter'] for item in equity_data]
        equity_levels = [item['equity'] for item in equity_data]
        asset_levels = [item.get('asset') for item in equity_data]
        liability_levels = [item.get('liability') for item in equity_data]

        if len(quarters) < 2:
            return jsonify({
                "error": {
                    "source": "DATA",
                    "message": "데이터 부족",
                    "detail": "최소 2개 분기 데이터가 필요합니다."
                }
            }), 400

        # 2. 금리 데이터 조회
        try:
            us10y_rates = get_us10y_rate(quarters)
        except Exception as e:
            return jsonify({
                "error": {
                    "source": "FRED",
                    "message": "US 10Y 금리 조회 실패",
                    "detail": str(e)
                }
            }), 500

        try:
            kr10y_rates = get_kr10y_rate(quarters)
        except Exception as e:
            return jsonify({
                "error": {
                    "source": "ECOS",
                    "message": "KR 10Y 금리 조회 실패",
                    "detail": str(e)
                }
            }), 500

        # 3. 데이터 병합
        us10y_levels = [us10y_rates.get(q) for q in quarters]
        kr10y_levels = [kr10y_rates.get(q) for q in quarters]

        # 4. 변화율 계산
        # 자본 변화율 (QoQ)
        equity_qoq = [None]
        for i in range(1, len(equity_levels)):
            if equity_levels[i-1] and equity_levels[i-1] != 0:
                change = (equity_levels[i] / equity_levels[i-1]) - 1
                equity_qoq.append(round(change, 6))
            else:
                equity_qoq.append(None)

        # 금리 변화 (소수로 변환)
        us10y_change = [None]
        for i in range(1, len(us10y_levels)):
            if us10y_levels[i] is not None and us10y_levels[i-1] is not None:
                change = (us10y_levels[i] / 100) - (us10y_levels[i-1] / 100)
                us10y_change.append(round(change, 6))
            else:
                us10y_change.append(None)

        kr10y_change = [None]
        for i in range(1, len(kr10y_levels)):
            if kr10y_levels[i] is not None and kr10y_levels[i-1] is not None:
                change = (kr10y_levels[i] / 100) - (kr10y_levels[i-1] / 100)
                kr10y_change.append(round(change, 6))
            else:
                kr10y_change.append(None)

        # 5. 듀레이션 계산
        us10y_duration_series, us10y_duration_summary = calculate_duration(equity_qoq, us10y_change)
        kr10y_duration_series, kr10y_duration_summary = calculate_duration(equity_qoq, kr10y_change)

        # 6. 억원 단위로 변환
        equity_level_billions = [round(e / 100000000, 1) if e else None for e in equity_levels]
        asset_level_billions = [round(a / 100000000, 1) if a else None for a in asset_levels]
        liability_level_billions = [round(l / 100000000, 1) if l else None for l in liability_levels]

        # 7. 응답 구성
        response = {
            "quarters": quarters,
            "equity_level": equity_level_billions,
            "asset_level": asset_level_billions,
            "liability_level": liability_level_billions,
            "us10y_level": us10y_levels,
            "kr10y_level": kr10y_levels,
            "equity_qoq": equity_qoq,
            "us10y_change": us10y_change,
            "kr10y_change": kr10y_change,
            "duration": {
                "us10y": {
                    "series": us10y_duration_series,
                    "summary": us10y_duration_summary
                },
                "kr10y": {
                    "series": kr10y_duration_series,
                    "summary": kr10y_duration_summary
                }
            },
            "company": COMPANY_MAP[company_id]['name']
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({
            "error": {
                "source": "SERVER",
                "message": "서버 오류가 발생했습니다",
                "detail": str(e)
            }
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """헬스 체크"""
    return jsonify({
        "status": "healthy",
        "dart_api": bool(DART_API_KEY),
        "ecos_api": bool(ECOS_API_KEY),
        "fred_api": bool(FRED_API_KEY)
    })


@app.route('/api/search_corp', methods=['GET'])
def search_corp():
    """회사명으로 corp_code 검색 (디버그용)"""
    keyword = request.args.get('keyword', '')
    if not keyword:
        return jsonify({"error": "keyword 파라미터 필요"}), 400

    if not DART_API_KEY:
        return jsonify({"error": "DART_API_KEY 미설정"}), 500

    try:
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
        response = requests.get(url, timeout=60)

        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            xml_filename = z.namelist()[0]
            with z.open(xml_filename) as f:
                tree = ET.parse(f)
                root = tree.getroot()

        results = []
        for corp in root.findall('.//list'):
            corp_name = corp.find('corp_name').text if corp.find('corp_name') is not None else ''
            if keyword in corp_name:
                corp_code = corp.find('corp_code').text if corp.find('corp_code') is not None else ''
                stock_code = corp.find('stock_code').text if corp.find('stock_code') is not None else ''
                results.append({
                    "corp_name": corp_name,
                    "corp_code": corp_code,
                    "stock_code": stock_code
                })

        return jsonify({"keyword": keyword, "results": results[:20]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# 메인 실행
# ============================================================================
if __name__ == '__main__':
    # 로컬 개발 모드
    # 프로덕션에서는 Gunicorn 사용:
    # gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
    app.run(debug=True, host='0.0.0.0', port=5000)
