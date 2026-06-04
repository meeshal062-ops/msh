# أداة تقرير مبيعات Syrve اليومي

هذه الأداة تعمل يوميًا عبر GitHub Actions، تدخل إلى Syrve، تحمل ملف Excel من التقارير، تحلل المبيعات الأساسية، ثم ترسل النتيجة بالبريد الإلكتروني، مع إشعار واتساب اختياري.

## ما الذي تفعله حاليًا؟

- تسجيل الدخول إلى: `https://half-million-co.syrve.app/`
- فتح رابط التقرير إذا تم وضعه في `SYRVE_REPORT_URL`
- الضغط على زر التحميل/Excel حسب `REPORT_DOWNLOAD_BUTTON_TEXT`
- تحليل Excel واستخراج:
  - إجمالي المبيعات
  - عدد الطلبات/الفواتير
  - متوسط الفاتورة
  - إجمالي الخصومات إذا وجد العمود
  - إجمالي الضرائب إذا وجد العمود
- إرسال تقرير Excel مختصر بالإيميل
- إرسال رسالة واتساب نصية اختيارية

## المطلوب لإكمال الربط مع حسابك

الأهم: بعد تسجيل الدخول يدويًا إلى Syrve، افتح صفحة التقرير المطلوب، ثم انسخ الرابط الكامل وضعه في Secret باسم:

`SYRVE_REPORT_URL`

إذا الرابط لا يفتح نفس التقرير مباشرة، أرسل لنا خطوات القائمة/صور الشاشة لنضيفها داخل `src/download_report.py`.

## إعداد GitHub Secrets

في GitHub Repository:

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

أضف القيم التالية:

```text
SYRVE_URL=https://half-million-co.syrve.app/navigator/index.html#/auth/login
SYRVE_USERNAME=اسم_المستخدم
SYRVE_PASSWORD=كلمة_المرور
SYRVE_REPORT_URL=رابط_صفحة_التقرير_بعد_تسجيل_الدخول
REPORT_DOWNLOAD_BUTTON_TEXT=Excel
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=بريدك
SMTP_PASSWORD=App Password وليس كلمة مرور البريد العادية
EMAIL_FROM=بريدك
EMAIL_TO=البريد_الذي_يستقبل_التقرير
WHATSAPP_ENABLED=false
```

> لا تضع كلمة المرور داخل الكود. استخدم GitHub Secrets فقط.

## توقيت التشغيل

الملف `.github/workflows/daily-report.yml` مضبوط على الساعة 06:00 صباحًا بتوقيت السعودية:

```yaml
- cron: "0 3 * * *"
```

لأن توقيت GitHub Actions يكون UTC، والسعودية UTC+3.

## ملاحظات مهمة

1. إذا كان الموقع يطلب OTP أو Captcha، التشغيل التلقائي اليومي قد يحتاج حل مختلف.
2. إذا تغيّر تصميم صفحة Syrve أو نص زر التحميل، قد نحتاج تعديل `REPORT_DOWNLOAD_BUTTON_TEXT` أو خطوات التنقل.
3. واتساب الرسمي يحتاج WhatsApp Cloud API من Meta. الإيميل أسهل وأثبت لإرسال ملف Excel.
