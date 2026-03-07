---
title: "Karsten Huttelmaier"
---

I build identity and security systems like service identity, PKI, OAuth and OIDC. I wrote code for years, consulted on human identity systems, then worked on Applied Cryptography at AWS and identity infrastructure at Snowflake.

---

## What I've Been Learning

The Gang of Four didn't invent design patterns. They noticed the same solutions kept appearing across unrelated codebases and gave them names. I think the same thing is happening with prompt engineering. The Catalyst is the clearest example I have.

### The Catalyst

I wrote a short prompt that produces exactly one transformative idea for any artifact, or says nothing at all. Not five suggestions. One committed idea, or an honest "I don't have one." I ran it on its own prompt and adopted the result immediately.

It works because of two ideas that keep showing up across everything I build.

#### Prompt Pipelines

The Catalyst isn't one step. It's two stages: first, envision the ideal end state and measure the gap. Then, generate one idea to close it. Stage 2 operates on stage 1's output, not on the original input.

That decomposition is the pattern. I used to keep asking the model to "make it better." Same prompt, another pass, hoping for improvement. That's retry logic. A pipeline is different: each step has its own specific purpose.

I built a [pipeline for studying for security interviews](https://github.com/kphutt/interview-prep) the same way. One stage generates the study plan, one generates deep content per topic, one distills that content into documents for NotebookLM's audio conversion. Each stage jumped in quality once separated. The model that produces generic output on combined problems produces sharp output on isolated ones.

#### Finding What's Actually Wrong

The Catalyst's first stage doesn't ask "what's wrong with this?" It uses [backcasting](https://en.wikipedia.org/wiki/Backcasting): start from what the ideal version looks like, then measure the distance back to where you are. How you frame the diagnosis determines what solutions you can see.

I kept getting a flat list of findings from AI review passes where "the thesis is missing" sat next to "the thesis doesn't land with the audience." I'd treat them the same way and waste effort. Once I started from the ideal version first, the distinction became obvious: the first one requires creating something new, the second requires reorienting something that exists. If the ideal does the same thing differently, that's structural. If the ideal IS a different thing, that's framing. Treating a framing problem as structural means you rebuild what just needed repositioning.

---

## What I've Been Building

| Project | What it is |
|---|---|
| [Interview Prep](https://github.com/kphutt/interview-prep) | Turns a topic list into study materials, podcasts, and a coaching bot |
| [Prompt Lenses](https://github.com/kphutt/prompt-lenses) | Review any document or code from a specific cognitive perspective |
| [Inbox Shepherd](https://github.com/kphutt/inbox-shepherd) | Tame a noisy inbox by classifying email the way you would if you had time |
| [Identity Lab](https://github.com/kphutt/identity-lab) | Learn identity protocols by running them, not reading about them |
| [Text Adventure](https://github.com/kphutt/text-adventure-v2) | A dungeon crawler in Go, because not everything has to be serious |
| [AI Toolkit](https://github.com/kphutt/ai-toolkit) | Guardrails that enforce standards instead of suggesting them |

---

[LinkedIn](https://linkedin.com/in/karstenhuttelmaier) ·
[GitHub](https://github.com/kphutt)
