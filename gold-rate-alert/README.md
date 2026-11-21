# Gold Rate Alert

This tool shows the price of 22K gold for 1 gram in Indian rupees (&#8377;), how the price
changed, and a simple estimate of the next official rate.

## What the alert shows

- The official rate of 22K gold for 1 gram, 8 grams and 10 grams. 24K is shown next to it.
- The change from the day before, in rupees and in percent.
- An estimate of the next official rate, with a range.
- All prices are in rupees and written the Indian way, for example &#8377;1,13,486.

## Where the numbers come from

- **Official rates of India:** the public page of IBJA (India Bullion and Jewellers
  Association), https://ibjarates.com/. IBJA gives an opening rate at about 12 noon and a
  closing rate after 5 PM, from Monday to Friday. There is no rate on Saturday, Sunday
  and public holidays. The rates are without GST and without making charges.
- **World gold price:** https://xaus.com/ (free, no account, no key).

The price in a shop in your city can differ a little, because each jeweller sets its own rate.

## The estimate

The program takes the last official rate and moves it by the change of the world gold
price since that rate was made. Example: the world price fell 1.4% after the last official
rate, so the estimate is the last official rate minus 1.4%.

- It is a simple calculation. Nobody can know the price in advance.
- It is not advice to buy or sell gold.
- The program checks every estimate against the real rate that comes later. The next
  alert shows the result in the line "Check".

## Run it on a Windows computer

1. Install Python 3 from https://www.python.org/downloads/ (only once).
2. Double-click run.bat.
3. The alert opens in the browser. It is also saved in the folder "out".

## The copy on GitHub

The same program runs on GitHub every day a little before 9:00 AM and 4:00 PM India time
(file .github/workflows/gold-rate.yml). It saves the alert in this folder under "out" and
opens an issue, so GitHub emails the alert to the owner of the repository. If GitHub does
not start it, a backup start tries again one hour later.

- The 9 AM alert shows the closing rate of the day before and an estimate for today.
- The 4 PM alert shows the opening rate of today and an estimate for the closing rate.
- On Saturday and Sunday there is no new rate, and the alert says so.
- GitHub can start a job some minutes late.

## Change the settings

Open config.json in Notepad.

| Setting | Meaning |
| --- | --- |
| main_purity, main_name | "916" and "22K". For 24K write "999" and "24K". |
| grams | The weights in the table, for example [1, 8, 10]. |
| gst_percent | The GST for the line "With GST". |
| email_when_nothing_changed | true: an email at every run. false: no email when nothing changed, for example on Sunday. |

## Important

The rates are read from the public page of IBJA for personal information only. For business
use, IBJA has its own data service. Before you buy or sell gold, ask your jeweller for the
price of the day.
