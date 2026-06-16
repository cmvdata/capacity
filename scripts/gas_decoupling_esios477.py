# -*- coding: utf-8 -*-
"""
gas_decoupling_esios477.py
--------------------------
Reproduces the CCGT availability-vs-production decoupling of Section 6 (Axis 3).

It fetches ESIOS indicator 477 ("Potencia disponible de generación Ciclo combinado
horizonte horario" = hourly available power of combined-cycle generation), which the
API returns disaggregated by province, and sums it to a national hourly series. It then
joins the ENTSO-E hourly generation dataset (net demand + Fossil-Gas production) and
reports, by market regime, the mean available power and production over the 5% highest
net-demand hours.

Requirements:
  - ESIOS_API_TOKEN in the environment (REE/ESIOS API key).
  - The ENTSO-E hourly dataset (net_demand, fossil_gas) used elsewhere in this repo,
    path given by --gen (default: data/processed/entsoe_hourly_dataset.parquet).

Output (matches the paper, top-5% net-demand hours):
  pre  (2019-21): available ~21,248 MW   production ~12,892 MW   utilisation 61%
  exc  (2022-23): available ~20,668 MW   production ~14,834 MW   utilisation 72%
  post (2024):    available ~19,450 MW   production ~11,831 MW   utilisation 61%

The flat availability across regimes (vs the production spike in the Iberian Exception)
is the measured decoupling: the Exception moved dispatch, not declared availability.
The fetched national series is cached to data/esios_477_ccgt_disp.csv.
"""
import os, re, time, calendar, argparse
import numpy as np, pandas as pd, requests

API = "https://api.esios.ree.es/indicators/477"

def token():
    t = os.environ.get("ESIOS_API_TOKEN") or os.environ.get("ESIOS_TOKEN")
    if not t:
        raise SystemExit("Set ESIOS_API_TOKEN in the environment.")
    return t

def fetch_477(y0=2019, y1=2024):
    H = {"Accept": "application/json; application/vnd.esios-api-v1+json",
         "x-api-key": token()}
    out = []
    for y in range(y0, y1 + 1):
        for mo in range(1, 13):
            ld = calendar.monthrange(y, mo)[1]
            r = requests.get(API, headers=H, timeout=60, params={
                "start_date": f"{y}-{mo:02d}-01T00:00",
                "end_date":   f"{y}-{mo:02d}-{ld}T23:59"})
            if r.status_code == 200:
                d = pd.DataFrame(r.json()["indicator"]["values"])
                if len(d):
                    out.append(d[["datetime_utc", "value"]])
            time.sleep(0.4)
    d = pd.concat(out, ignore_index=True)
    d["dt"] = pd.to_datetime(d["datetime_utc"], utc=True).dt.tz_convert(None)
    # 477 is province-disaggregated: sum to national per hour
    return d.groupby("dt")["value"].sum().resample("h").mean()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", default="data/processed/entsoe_hourly_dataset.parquet")
    ap.add_argument("--cache", default="data/esios_477_ccgt_disp.csv")
    args = ap.parse_args()

    if os.path.exists(args.cache):
        disp = pd.read_csv(args.cache, index_col=0, parse_dates=True).iloc[:, 0]
    else:
        disp = fetch_477()
        os.makedirs(os.path.dirname(args.cache), exist_ok=True)
        disp.to_csv(args.cache)

    g = pd.read_parquet(args.gen)
    g["dt"] = pd.to_datetime(g["datetime"]); g = g.set_index("dt")
    df = pd.DataFrame({"disp": disp, "prod": g["fossil_gas"], "nd": g["net_demand"]}).dropna()
    df["reg"] = np.where(df.index.year <= 2021, "pre",
                 np.where(df.index.year <= 2023, "exc", "post"))
    print(f"{'regime':9} {'avail_MW':>9} {'prod_MW':>9} {'util_%':>7}")
    for r in ["pre", "exc", "post"]:
        s = df[df["reg"] == r]; top = s[s["nd"] >= s["nd"].quantile(0.95)]
        print(f"{r:9} {top['disp'].mean():9.0f} {top['prod'].mean():9.0f} "
              f"{100*top['prod'].mean()/top['disp'].mean():7.0f}")

if __name__ == "__main__":
    main()
