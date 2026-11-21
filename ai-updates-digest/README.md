# AI Updates Digest (local version)

This program runs on your own computer. It collects current AI updates from public
sources, keeps what is recent and trending, and saves a digest. If you set up Gmail,
it also emails the digest to you.

It needs Python 3 only. Nothing else has to be installed.

## 1. Run it

Double-click **run.bat**.

A window shows each source and how many items it gave. After about a minute the
digest opens in your browser. It is also saved in the `out` folder:

- `out\latest.html` - the newest digest
- `out\digest-YYYY-MM-DD.html` and `.md` - one per day
- `out\last_run.json` - which sources worked and which failed

## 2. Turn on email (optional)

1. Turn on 2-Step Verification for your Google account.
2. Create an app password at https://myaccount.google.com/apppasswords
   (any name, for example "AI digest"). Google shows 16 characters.
3. Open **email_settings.txt**, paste the 16 characters after
   `GMAIL_APP_PASSWORD=` and save.
4. Double-click **test_email.bat**. You should get a short test email.

After that, every run sends the digest by email. Keep `email_settings.txt` private:
do not share it or put it in a zip you send to someone.

## 3. Run it every day (optional)

Double-click **schedule_daily.bat**. Windows then runs the digest every day at 08:30
while you are signed in. A small black window appears for about a minute.

- The computer must be switched on at 08:30.
- The log is in `out\scheduled_log.txt`.
- To stop it, double-click **unschedule.bat**.
- To change the time, edit `08:30` in `schedule_daily.bat` and run it again.

## 4. The five sections

| Section | Where it comes from |
| --- | --- |
| Releases | News feeds of OpenAI, Google, Microsoft, Mistral, Hugging Face and a community feed of Anthropic news |
| Trending AI tools | "Show HN" posts on Hacker News and new GitHub repositories that gain stars fast |
| What users are saying | Popular Hacker News stories about AI, DEV Community posts, Simon Willison's blog, Reddit r/LocalLLaMA |
| Reusable code | New agent skills, templates, cookbooks and examples on GitHub |
| AI for integration | Azure Integration Services blog, Azure updates, news about Logic Apps, SAP Integration Suite, TIBCO and MuleSoft, and Azure sample repositories |

## 5. How it filters

- **Recent only.** It looks back 36 hours (84 hours on Monday). Items without a date are dropped.
- **Trending only.** Hacker News stories need 80 points (30 for "Show HN"). DEV posts need 20 reactions.
  GitHub repositories need a minimum number of stars, and repositories younger than 3 days are skipped.
- **No repeats.** An item that was in an earlier day's digest is not shown again for 30 days
  (`out\seen.json` remembers them).
- **No money news.** Titles about funding, valuations and stock prices are left out.
- **No duplicates.** The same link or the same headline appears once.

Trending does not mean trusted. Read a repository before you run its code.

## 6. Change what it collects

Open **config.json** in a text editor.

- `window_hours` - how far back to look.
- `max_items_per_section` - how many items per section.
- `hacker_news.min_points` - raise it for fewer, bigger stories.
- `feeds` - add any RSS or Atom address. Each feed has a `name`, a `section`
  (`releases`, `tools`, `community`, `code` or `integration`) and one or more `urls`.
  Optional: `include` (keep only items with these words), `max_items`, `window_hours`.
- `github.queries` - GitHub search queries. `{d7}`, `{d14}`, `{d30}` and `{d90}` are
  replaced by the date that many days ago.
- `ai_terms` - the words that make a Hacker News title count as an AI story.

If you break the file, delete it: the program then uses its built-in settings.

## 7. The AI picks

After the digest is made, **run.bat** also starts a small AI model on this computer
(file `ai_brief.py`). The model puts a box "AI picks" at the top of the digest: the items
that fit you best, each with one short line.

- It needs the free program Ollama (https://ollama.com), installed and running.
  Without it this step is skipped and the digest is unchanged.
- No account and no key are needed. Nothing is sent to an AI service.
- The first time, it asks before it downloads the model (about 2.5 GB, one time).
- The model is small and can make mistakes. Check the links.
- To change who the picks are for, edit `reader` in **ai_settings.json**.
  To switch the step off, set `"enabled": false` there.

The rest of the digest is not written by AI. It shows the title, the numbers (points,
comments, stars) and the publisher's own first lines. The scheduled task in Claude does
write summaries. You can keep both; then you get two emails a day.

## 8. If something goes wrong

| What you see | What to do |
| --- | --- |
| "Python 3 was not found" | Install Python from https://www.python.org/downloads/ and run again. |
| Every source says FAILED | Check the internet connection. On an office network that needs a proxy, add `HTTPS_PROXY=http://host:port` to `email_settings.txt`. |
| One or two sources say FAILED | The digest still works. A site may be down or blocked on this network. To remove a source, set `"enabled": false` for it in `config.json`. |
| "Gmail did not accept the sign-in" | Use the 16-character app password, not your normal password. |
| "Could not reach Gmail" | Some office networks block outgoing mail. The digest file is still saved. |
| "Nothing new since the last digest" | Everything recent was already sent. Run `run.bat --all` in a command window to see it again. |

## 9. Commands

```
python ai_digest.py               collect, save, email if set up
python ai_digest.py --open        the same, then open the digest in the browser
python ai_digest.py --no-email    collect and save only
python ai_digest.py --hours 72    look back 72 hours
python ai_digest.py --all         also show items sent on earlier days
python ai_digest.py --test-email  send a short test email
python ai_digest.py --selftest    check the program without using the internet
```
