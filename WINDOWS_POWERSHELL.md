# تشغيل ZeiadYazajiGPT على Windows 11 باستخدام PowerShell

هذا الملف يشرح أوامر PowerShell المطلوبة لتشغيل المشروع محليًا على لابتوب Windows 11.

> **مهم:** شغّل PowerShell كمستخدم عادي. لا تحتاج إلى Run as Administrator.

## 1) التأكد من وجود Git و Python 3.11

افتح PowerShell ونفّذ:

```powershell
git --version
py -3.11 --version
```

إذا كان Git غير موجود:

```powershell
winget install --id Git.Git -e
```

إذا كان Python 3.11 غير موجود:

```powershell
winget install --id Python.Python.3.11 -e
```

بعد تثبيت Git أو Python أغلق PowerShell وافتحه من جديد.

---

## 2) أول تشغيل للمشروع

انسخ الأوامر التالية إلى PowerShell بالترتيب:

```powershell
Set-Location "$HOME\Desktop"

git clone https://github.com/mmaatrix/ZeiadYazajiGPT.git

Set-Location ".\ZeiadYazajiGPT"

py -3.11 -m venv .venv

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip setuptools wheel

python -m pip install -e .

ZeiadYazajiGPT setup

ZeiadYazajiGPT
```

### ماذا يفعل `ZeiadYazajiGPT setup`؟

يقوم تلقائيًا بـ:

- تثبيت متطلبات الصوت الإضافية.
- تنزيل ملفات الـ avatars / voices / photos المطلوبة.
- تنزيل ملفات الواجهة الإضافية.
- إنشاء اختصار **ZeiadYazajiGPT** على سطح المكتب.

قد يستغرق أول Setup عدة دقائق لأنه ينزل مئات الميغابايت من الملفات.

---

## 3) التشغيل في المرات التالية

بعد اكتمال التثبيت أول مرة، يكفي:

```powershell
Set-Location "$HOME\Desktop\ZeiadYazajiGPT"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\.venv\Scripts\Activate.ps1

ZeiadYazajiGPT
```

أو شغّل اختصار **ZeiadYazajiGPT** الذي تم إنشاؤه على سطح المكتب.

---

## 4) تحديث المشروع من GitHub

لتنزيل آخر تعديلات من المستودع:

```powershell
Set-Location "$HOME\Desktop\ZeiadYazajiGPT"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\.venv\Scripts\Activate.ps1

git pull

python -m pip install -e .

ZeiadYazajiGPT
```

---

## 5) إذا لم يتعرف PowerShell على الأمر ZeiadYazajiGPT

تأكد أولًا أن البيئة الافتراضية مفعلة:

```powershell
.\.venv\Scripts\Activate.ps1
```

ثم جرّب:

```powershell
python -m ZeiadYazajiGPT
```

---

## 6) تشغيله داخل المتصفح بدل نافذة البرنامج

من داخل مجلد المشروع وبعد تفعيل البيئة:

```powershell
python -m uvicorn ZeiadYazajiGPT.main:app --host 127.0.0.1 --port 8000
```

ثم افتح:

```powershell
Start-Process "http://127.0.0.1:8000/"
```

لإيقاف السيرفر اضغط:

```text
Ctrl + C
```

---

## 7) إذا ظهر خطأ ExecutionPolicy

نفّذ داخل نفس نافذة PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

ثم:

```powershell
.\.venv\Scripts\Activate.ps1
```

هذا التغيير مؤقت لهذه النافذة فقط ولا يغيّر إعداد Windows بشكل دائم.

---

## 8) إعادة الـ Setup إذا حدث خطأ في الملفات المحمّلة

```powershell
Set-Location "$HOME\Desktop\ZeiadYazajiGPT"

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\.venv\Scripts\Activate.ps1

ZeiadYazajiGPT setup --force
```

ثم:

```powershell
ZeiadYazajiGPT
```

---

## 9) مفتاح الـ API

الإصدار الحالي من المشروع يستخدم **Gemini Live API**.

لا تحتاج إلى إنشاء ملف `.env`. عند إنشاء Profile داخل البرنامج لأول مرة سيطلب منك إدخال Gemini API Key داخل الواجهة.

---

## أسرع أوامر لأول تشغيل

إذا كان Git و Python 3.11 مثبتين أصلًا، فهذا هو البلوك الكامل:

```powershell
Set-Location "$HOME\Desktop"
git clone https://github.com/mmaatrix/ZeiadYazajiGPT.git
Set-Location ".\ZeiadYazajiGPT"
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
ZeiadYazajiGPT setup
ZeiadYazajiGPT
```
