# Prenos projektu na nove PC

Tento navod je pro bezpecny presun AIInvest na jiny Windows pocitac.

Pouzij dve vrstvy:

1. Repo z GitHubu
2. Lokalni data mimo Git

Repo obsahuje kod a skripty. Lokalni data obsahuji hlavne:

- `PRJCT/python-core/.env`
- modely pro llama.cpp
- MongoDB data
- reporty a lokalni vystupy

## Rychly plan

### Na starem PC

1. Zastav bezici stack.
2. Vytvor migracni bundle.
3. Prenes bundle na nove PC.

### Na novem PC

1. Nainstaluj prerekvizity.
2. Naklonuj repo z GitHubu.
3. Obnov bundle.
4. Doinstaluj Python a Node zavislosti.
5. Spust stack.

## 1. Stare PC

Nejdriv zastav aplikaci:

```powershell
cd C:\aiinvest
powershell -ExecutionPolicy Bypass -File .\PRJCT\stop_aiinvest.ps1
```

Pak vytvor bundle.

Minimalni varianta bez databaze a reportu:

```powershell
cd C:\aiinvest
powershell -ExecutionPolicy Bypass -File .\PRJCT\migrate_aiinvest_to_new_pc.ps1 -Mode Export -IncludeModels
```

Plna varianta i s MongoDB daty, lokalnim Mongo serverem a reporty:

```powershell
cd C:\aiinvest
powershell -ExecutionPolicy Bypass -File .\PRJCT\migrate_aiinvest_to_new_pc.ps1 -Mode Export -IncludeModels -IncludeDatabaseData -IncludeMongoServer -IncludeReports -CreateZip
```

Poznamky:

- Bundle se standardne vytvori do `C:\aiinvest\_transfer\...`
- Pri `-CreateZip` vznikne i `.zip`
- Pokud exportujes znovu do stejneho mista, pridej `-Force`

## 2. Nove PC

### Prerekvizity

Nainstaluj:

- Git
- Python 3.10
- Node.js 18+
- .NET 8 SDK nebo desktop runtime

Pokud nebudes prenaset `DTB\MongoDB\server`, nainstaluj i MongoDB 6.0.

### Klon repa

```powershell
git clone https://github.com/ViitTek/aitrade.git C:\aiinvest
cd C:\aiinvest
```

### Obnova bundle

Pokud mas bundle jako adresar:

```powershell
powershell -ExecutionPolicy Bypass -File .\PRJCT\migrate_aiinvest_to_new_pc.ps1 -Mode Import -BundlePath "D:\AIInvest-transfer-20260323-xxxxxx"
```

Pokud mas zip, nejdriv ho rozbal a pak pouzij cestu k rozbalenemu adresari.

Kdyz uz v cili nejake soubory existuji a chces je prepsat:

```powershell
powershell -ExecutionPolicy Bypass -File .\PRJCT\migrate_aiinvest_to_new_pc.ps1 -Mode Import -BundlePath "D:\AIInvest-transfer-20260323-xxxxxx" -Force
```

## 3. Instalace zavislosti na novem PC

### Python

```powershell
cd C:\aiinvest\PRJCT\python-core
C:\Program Files\Python310\python.exe -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt
```

### Dashboard

```powershell
cd C:\aiinvest\PRJCT\dashboard
npm install
```

## 4. Spusteni

```powershell
cd C:\aiinvest\PRJCT
.\Start-AIInvest.cmd
```

## 5. Co zkontrolovat po startu

1. API bezi.
2. Dashboard bezi.
3. MongoDB bezi.
4. `bot/status` vraci `running`.
5. Pokud pouzivas IBKR, over `ibkr/status`.
6. Pokud pouzivas cross-asset shadow, over, ze pribyvaji nove `market_candles`.

Prakticke kontroly:

```powershell
curl http://127.0.0.1:8110/bot/status
curl http://127.0.0.1:8110/ibkr/status
curl http://127.0.0.1:8110/market-data
```

## Co se standardne NEprenasi z repa

Tyhle veci se maji na novem PC vytvorit nebo nainstalovat znovu:

- Python `venv`
- `dashboard/node_modules`
- bezne build vystupy
- docasne cache

## Doporuceni

Pro nejbezpecnejsi migraci:

- kod vzdy ber z GitHubu
- `.env` a modely prenes bundlem
- MongoDB data prenes jen kdyz chces zachovat historii, reporty a stare vysledky
- po prenosu udelej jeden test start a jeden test stop
