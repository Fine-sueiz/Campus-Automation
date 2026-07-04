# Campus Automation
This project can identify the articles on public accounts, capture the important information as the starting time of an activity, the location, sign-up form link. After that, it will send an email to you to ask about whether you want to participate in it. If your answer is yes, it will fill in the questionnaire based on the information you have given. And if you sign up successfully, this event will also be added to our custom calendar to remind you when to join.

## Subproject

- `monitor/`: Campus public account opportunity monitoring service. It scans articles from public accounts/RSS sources, identifies opportunities like work-study and volunteer services based on keywords and model scoring, and then initiates a confirmation process via email or interface.
- `schedule/`: Personal schedule and course arrangement service. It provides a schedule interface and can receive candidate events written by external systems.
- `questionnaire-helper/`: A helper for automatically filling out questionnaires and forms. It’s responsible for opening forms, filling in fields, and completing automated submission processes according to the configuration; the public version doesn’t include the MinGit toolchain, browser data, logs, or real form data.

## Project relationship
The typical workflow is: `monitor` scans public account articles -> scores and filters opportunities -> confirms via email or manually -> calls `schedule` to write into the calendar; `questionnaire-helper` is an independent form-filling tool that can also be called by the upper-level process when a registration form needs to be filled out.

## Configuration Instructions
- `monitor/.env.example` keeps the original config keys and structure, but real emails, SMTP auth codes, API keys, Feishu, Cloudflare, and other secrets have all been replaced with placeholders.
- `monitor/config/app.yml`, `monitor/config/schedule.yml`, and `monitor/config/personal_availability.md` have been replaced with general examples.
- `schedule/qq_sync_config.example.json` is a public example; the real `data/qq_sync_config.json` is not released with the repo.
- The `.gitignore` in the root and sub-projects will continue to ignore `.env`, `data/`, `logs/`, databases, virtual environments, and build artifacts.

For more deployment and integration instructions, check out:

- `monitor/docs/server_migration.md`
- `monitor/docs/n8n_integration.md`

## About design trade-offs and safety
Q: Why didn't you choose to detect activity and just submit the survey?
A: Some volunteer activities might not be very valuable. If we just submit the application directly, it might not be the kind of activities we want to do. That would be a waste of time, and if we forget about the activity later, it could get us blacklisted and affect signing up for future activities. So we came up with sending you an email to confirm whether you want to sign up or not. It can let you know this event exists and also let you decide what exactly to choose.
Q: What if the program does something wrong while I'm still testing it?
A: It runs in dry-run/fake mode by default—emails are just logged, not actually sent, and forms are just simulated, not really submitted. You can watch it run through completely to make sure everything’s fine, then manually switch to actually sending.