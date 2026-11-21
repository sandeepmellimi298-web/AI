# Bank Jobs Alert

This tool reads news about bank recruitment in India every day and writes a short alert
with three groups: government banks, private banks and other banks.

## Run it on a Windows computer

1. Install Python 3 from https://www.python.org/downloads/ (only once).
2. Double-click run.bat.
3. The alert opens in the browser. It is also saved in the folder "out".

Other ways to start it, from a command window in this folder:

    run.bat --all        also show items that were sent on earlier days
    run.bat --hours 72   look back 72 hours

## What it keeps

- A headline must name a bank and must be about jobs (recruitment, vacancy, apply online, hiring).
- Headlines about admit cards, results, cut-offs and exam dates are left out.
- The same notice from many websites is shown once.
- A notice that was sent on an earlier day is not sent again for 30 days.
- At most 12 lines for each group, and at most 2 lines for one bank.

## Change the settings

Open config.json in Notepad. You can change the news searches, the words, the list of banks
and the limits.

## The AI picks

After the alert is made, run.bat also starts a small AI model on this computer
(file ai_brief.py). The model puts a box "AI picks" at the top of the alert: the notices
that fit a job seeker best, each with one short line.

- It needs the free program Ollama (https://ollama.com), installed and running.
  Without it this step is skipped and the alert is unchanged.
- No account and no key are needed. Nothing is sent to an AI service.
- The first time, it asks before it downloads the model (about 2.5 GB, one time).
- The model is small and can make mistakes. Check the links.
- To switch it off, open ai_settings.json in Notepad and set "enabled" to false.

## The copy on GitHub

The same program runs on GitHub every day a little after 8:00 AM India time
(file .github/workflows/bank-jobs.yml). It adds the AI picks, saves the alert in this
folder under "out" and opens an issue, so GitHub emails the alert to the owner of the
repository.

## Important

The lines are news headlines. Nobody checked them on the bank's website.
Before you apply, open the official website of the bank. Official websites of Indian banks
end in .bank.in. Apply and pay the fee only there. No bank asks for money to give a job.
