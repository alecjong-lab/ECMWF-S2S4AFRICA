```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║         .    _.----~~~~~~~7                                                  ║
║             /              ~-..-~~--..--.                                    ║
║       .'.'.'                             `.                                  ║
║         .~         ███████╗██████╗ ███████╗ \                                ║ 
║       .'           ██╔════╝╚════██╗██╔════╝   .                              ║
║   .   (            ███████╗ █████╔╝███████╗    \                             ║ 
║ '.    )            ╚════██║██╔═══╝ ╚════██║     `.                           ║
║   '  (             ███████║███████╗███████║       ~-.                        ║ 
║       \           ╚══════╝╚══════╝╚══════╝            ~-~~7                  ║
║        `.       __.._     F O R  A F R I C A             '                   ║  
║          ~-.--~~     ~--.                              /                     ║
║                         ;                          .-~                       ║
║                         (                        .~                          ║
║                          `.                    .'                            ║
║                            ;                   ;                             ║
║                            `.                  `       _                     ║
║                             )                   )     / )                    ║
║                            (                 _.-'  .-' .'                    ║
║                            `.               (      )   /                     ║
║                              7             _;      < _/                      ║
║                               \           /         ~                        ║ 
║                                \         /                                   ║
║                                 `. __..-'                                    ║
║                                   ~                                          ║ 
╚══════════════════════════════════════════════════════════════════════════════╝
```

ECMWF subseasonal forecast plots for **9 African countries**,
updated automatically every day at **00:16 UTC**.

---

### Countries covered

`Angola` · `Botswana` · `Ethiopia` · `Ghana` · `Kenya`
`Madagascar` · `Namibia` · `Senegal` · `Zambia`

---

### Plot types

All plots are stored in the [`/plots`](./plots) folder, organised by country.

| # | Type | Description |
|---|------|-------------|
| i | **Accumulated precipitation** | Weekly, dekadal, and monthly totals; includes change in weekly accumulated precipitation |
| ii | **Exceedance percentages** | Fraction of ensemble members exceeding the 25th, 50th, or 75th climatological quantile |
| iii | **Anomalies** | Forecast deviation from climatological percentiles |
| iv | **Tercile probabilities** | Probability of the forecast falling in the below-normal, near-normal, or above-normal category |
| v | **Meteograms** | Ensemble precipitation vs. climatology for the two most populated cities |
| vi | **Extreme Forecast Index (EFI)** | Weekly EFI with Shift of Tails (SoT) as contours |

---

### Update schedule

Plots are regenerated daily via GitHub Actions. 

![last update](https://img.shields.io/github/last-commit/alecjong-lab/ECMWF-S2S4AFRICA?label=last%20update)

---

### Data source

Forecast data from [ECMWF](https://www.ecmwf.int/) subseasonal ensemble products.
