"""Build tagger training data.

Clean sentences come from your own markdown notes (DICTATION_NOTES_DIR) plus hand-written casual sentences
and slot templates. We inject disfluencies with exact word labels:
K keep, D delete, E needs edit (back-reference correction, goes to the editor).

Usage: python tagger/make_data.py [--n 50000] [--dev 2500] [--seed 7]
"""
import argparse
import glob
import json
import os
import random
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.expanduser(os.environ.get("DICTATION_NOTES_DIR", ""))  # empty: no notes, hand-written sentences only
SKIP = ("_to_delete", "Archive", ".smart-env")
TERM = ".?!"

# ---------------------------------------------------------------- vault text


def clean_markdown(text):
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"%%.*?%%", " ", text, flags=re.S)
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith(">!"):
            continue
        if re.match(r"^[-*_=]{3,}$", s) or s.count("|") >= 2:
            continue
        s = re.sub(r"^(\s*[-*+]\s+(\[.\]\s+)?|\s*\d+[.)]\s+|>+\s*)", "", s)
        s = re.sub(r"!\[\[.*?\]\]", "\x00", s)
        s = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
        s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"`[^`]*`", "\x00", s)
        s = re.sub(r"(\*\*|__|\*|_|==)", "", s)
        s = re.sub(r"<[^>]+>", " ", s)
        s = s.replace("—", ", ").replace("–", ", ").replace(" -- ", ", ")
        s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        out.append(s)
    return "\n".join(out)


def good_sentence(s):
    if "\x00" in s:
        return False
    if re.search(r"https?://|www\.|@\w+\.|\{\{|\}\}|[\\/=<>{}\[\]^~#$%&*|_]|\.md|\.py", s):
        return False
    s = s.strip()
    words = s.split()
    if not 4 <= len(words) <= 30:
        return False
    alpha = sum(1 for w in words if re.fullmatch(r"[A-Za-z][A-Za-z'\-]*[,.;:?!]?", w))
    if alpha / len(words) < 0.8:
        return False
    if not s[0].isupper() and not s[0].isdigit():
        return False
    if s.count('"') % 2 or s.count("(") != s.count(")"):
        return False
    if re.search(r"[,;:]$", s):
        return False
    if s.endswith(":"):
        return False
    return True


def vault_sentences():
    sents = []
    for p in (glob.glob(VAULT + "/**/*.md", recursive=True) if VAULT else []):
        if any(k in p for k in SKIP):
            continue
        try:
            raw = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if len(raw) > 400_000:
            continue
        txt = clean_markdown(raw)
        for line in txt.split("\n"):
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line.strip()):
                s = s.strip().strip('"').strip("()").strip()
                if good_sentence(s):
                    sents.append(s)
    seen, uniq = set(), []
    for s in sents:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


# ------------------------------------------------- hand-written casual text

GENERIC = """
I think we should probably move the meeting to next week.
Can you send me the notes from yesterday when you get a chance?
I need to pick up groceries after class and then call my mom.
The problem is that I keep putting off the lab report.
We could try the new pizza place on Main Street if you want.
I'm not sure if the professor posted the study guide yet.
Remind me to email the landlord about the heater.
It looks like rain, so let's bring umbrellas.
I really like the second design better than the first one.
Do you know where I left my charger?
You know what I mean when I say the app feels slow.
I mean it, we really need to fix the login bug today.
That was actually a pretty good idea.
There is no way we finish this by Friday.
No, I can't do Saturday because of work.
Sorry about the late reply, my phone died.
We can do pizza or tacos, whichever you prefer.
Something like that would work for the demo.
I would like to schedule a call with the client sometime this week.
The deadline is coming up fast and I have barely started.
Let's make that work somehow.
Make sure the door is locked before you leave.
I just got off the phone with the bank and everything is fine.
Maybe we should look at renting a bigger place next year.
She said the package should arrive on Thursday.
I have to finish the quiz before midnight tonight.
The new keyboard feels great but the space bar is loud.
Can we push the dentist appointment back a few days?
I want to rewrite the intro paragraph because it sounds stiff.
He told me the flight was delayed by two hours.
Don't forget to bring your ID to the front desk.
Honestly I think the second half of the video drags.
The train leaves at seven so we need to be there early.
I'll text you when I'm outside.
We need more chairs for the party on Saturday.
Let me know if the Wi-Fi password changes.
I was thinking about starting a small side project this winter.
The coffee machine on the third floor is broken again.
It would be nice to get everyone together before the semester ends.
My laptop battery only lasts about three hours now.
Could you check the budget numbers before the call?
I spent most of the afternoon cleaning up my inbox.
The recipe says to bake it for forty minutes.
We should probably ask Jake whether he wants to come along.
I think the printer is out of ink again.
This week is packed, so let's talk on Monday.
I'm going to the gym after work, then I'll start dinner.
She likes the blue one more than the green one.
I like that you thought about the schedule ahead of time.
Tell her that the keys are on the kitchen table.
It was a long day and I'm ready to go to bed.
I forgot to tell you that the meeting got moved.
The movie starts at nine, but we can grab food first.
He wants to know if the rent is due on the first or the fifth.
Thanks for helping me move last weekend.
I need a new pair of running shoes before the race.
Our group still has to finish the slides and the speaker notes.
Pretty sure the store closes at six on Sundays.
There are three things I want to get done today.
First, email the TA about the extension.
Second, finish reading chapter four.
Third, go for a run before it gets dark.
My brother is flying in on Friday and staying for the weekend.
The roof is leaking again over the back bedroom.
I'd like to try making bread from scratch this weekend.
It feels like the whole semester went by in a week.
He looks like his dad, honestly.
I don't know what to get her for her birthday.
We're running low on milk and eggs.
Can you believe how fast the price went up?
I have a question about the second assignment.
Let's circle back on this after lunch.
The car needs an oil change and new wipers.
She's coming over around six with the kids.
Just a quick reminder that the form is due tomorrow.
I think I left the stove on, so I'm heading back.
The file is in the shared folder under the project name.
We talked about it for an hour and still couldn't decide.
I have never seen so many people at the library.
Add a note to call the plumber on Monday morning.
Let's try again once the update finishes installing.
I was going to cook but I'm way too tired.
The best part of the trip was the drive up the coast.
It's supposed to snow tomorrow, so the roads might be bad.
Ask him if he can cover my shift on Tuesday.
You should really talk to the advisor before you sign up.
I'm still waiting on the receipt from the store.
Our flight lands at noon and the shuttle leaves at one.
That's the third time this week the app crashed.
He made a great point about the pricing.
The sample size is too small to say anything useful.
I want to go over the notes one more time before the exam.
Is there any chance you could drive me to the airport?
I put the leftovers in the fridge on the top shelf.
She asked whether we want the window seat.
Please let me know what time works best for you.
We need to decide soon because the tickets sell out fast.
The interview went better than I expected.
I can't find my keys anywhere.
Do you want me to grab you a coffee on the way over?
The band was loud but the crowd loved it.
We should replace the batteries in the smoke detector.
I'll have the draft ready for review by the end of the day.
My phone keeps telling me the storage is full.
They said the repair would take about a week.
I need to renew my parking pass before it expires.
Can you remind me what the password hint was?
It took forever to get through the line at the airport.
The garden is finally starting to grow.
He asked me to read over his cover letter tonight.
I'm trying to cut back on caffeine this month.
Let's keep the budget under two thousand dollars.
The hardest part is deciding what to cut.
I think it works better if we start with the demo.
We're meeting in the lobby at eight sharp.
She never answered my last three texts.
I keep forgetting to water the plants.
They want us to submit the invoice by Friday.
Looks like we have a few open slots next week.
Everything felt a little rushed at the end.
The pitch is solid, we just need a better closing slide.
I've been meaning to clean out the garage for months.
The hotel has a pool and free breakfast.
Please bring a laptop, a charger, and a notebook.
I liked the first draft more than the final one.
It sounds like a plan to me.
I'm not a fan of the new layout.
He's like the nicest guy I know.
It was like, a thousand degrees in that room.
We bought apples, oranges, and a few lemons.
Her sister and her cousin are visiting from Boston.
He gave the book to her and the pen to me.
I told him that she would call back.
Give the report to him by noon.
Send the invite to her and copy the whole team.
The tickets cost thirty dollars each.
I'll be there at four thirty.
Meet me at the library on Wednesday at three.
We have five boxes and two bags to load.
The seminar is on the twelfth of March.
I paid forty dollars for the parking.
Jordan said he could do Thursday morning.
Pick up the package from the post office on Elm Street.
Our dinner reservation is at seven for four people.
The blue folder goes on the top shelf.
I'll call Sarah tomorrow about the contract.
Order two large pizzas and a salad.
The shipment should include ten chairs and three tables.
We're driving to Chicago on Saturday morning.
Pat said the meeting is in room two fourteen.
The invoice total is four hundred and fifty dollars.
Email Dana the updated numbers by Monday.
I want the red jacket, not the black one.
Honestly it's fine either way.
Yeah, that works for me.
Okay, so here's the plan for tomorrow.
Right, and then we can look at the budget.
Anyway, I'll figure it out later.
Alright, let's get started on the slides.
Oh, and don't forget the charger.
Well, it depends on the weather.
So the main idea is to keep things simple.
Wait, I think I sent that to the wrong person.
Actually, that reminds me of something from last week.
Hold on, let me check my calendar.
Basically we need one clear owner for each task.
Right now the biggest issue is the build time.
Look, I'm just saying we should plan ahead.
By the way, the keys are under the mat.
Fine, but only if we leave early.
One more thing, the vendor wants a signed copy.
Thanks, that helps a lot.
Sorry, I can't make it on Friday.
Sorry for the mess in the kitchen.
Sorry about the confusion with the schedule.
No, that's not what I meant.
No problem at all, take your time.
Actually, I changed my mind about the trip.
Actually, can you send me that link again?
Wait for me at the entrance.
Hold on, I'll be right there.
Is it the red one or the blue one?
I meant to call you yesterday.
Rather than driving, let's take the train.
I think the answer is no.
She said no to the offer.
We can meet Tuesday or Wednesday, whichever works for you.
Do you want coffee or tea?
Wait, how many people are coming?
Well, I guess that settles it.
I mean, the whole schedule is a mess.
Make that call before lunch.
Change the password before you log in.
Make it quick, the bus is almost here.
You know that feeling when you walk into a room and forget why?
You know the place on Main Street with the blue door?
You know what, let's just order pizza.
You know how the printer jams every time we need it?
You know I would help if I could.
You know the deal, we leave at seven.
You know that guy from the second floor?
You know where the spare key is, right?
You know her sister works at the bank.
I mean what I said about the deadline.
I mean it when I say the budget is tight.
I mean the red one, not the blue one.
I mean every word of the apology.
I mean to finish the draft tonight.
I mean that sincerely, thank you for the help.
Like I said, the meeting is on Friday.
Like I said earlier, we need a bigger room.
Like the last time, we should book early.
Like most people, I check my email first thing.
Like her brother, he plays the drums.
Like it or not, the deadline is Monday.
Like we talked about, I sent the invoice yesterday.
I'm sorry but I can't make it tonight.
I'm so sorry about the mix up with the tickets.
I'm sorry, I didn't catch that last part.
Sorry, what time did you say the meeting was?
I'm really sorry, my phone was off.
I'm sorry to hear that the trip got canceled.
And then we went to the beach for the rest of the day.
And then the bus finally showed up around noon.
So then I told him that we were out of time.
Then the whole thing fell apart on Friday.
""".strip().split("\n")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
NAMES = ["Pat", "Sarah", "Jake", "Dana", "Priya", "Tom", "Alex", "Maria", "Chris", "Jordan", "Emily", "Ben", "Lena", "Omar", "Katie", "Dave", "Nina", "Sam", "Rachel", "Luis"]
PLACES = ["the library", "the office", "the gym", "my place", "your place", "the cafe", "campus", "downtown", "the airport", "the student center", "Chicago", "Boston", "the park", "the lobby", "room 214", "the lab"]
TIMES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "noon", "2:30", "3:30", "4:15", "6:30", "7:45", "9:30", "3 PM", "5 p.m.", "8 AM", "10 a.m."]
NUMS = ["two", "three", "four", "five", "six", "seven", "eight", "ten", "twelve", "fifteen", "twenty", "2", "3", "4", "5", "6", "8", "10", "12", "15", "20", "30", "40", "50"]
MONEY = ["$20", "$30", "$40", "$45", "$50", "$75", "$100", "$120", "$200", "$250", "$500", "twenty dollars", "forty dollars", "fifty dollars", "a hundred dollars"]
COLORS = ["red", "blue", "green", "black", "white", "gray", "yellow", "orange", "purple"]
ITEMS = ["milk", "eggs", "bread", "butter", "apples", "coffee", "rice", "cereal", "chicken", "pasta", "tomatoes", "cheese", "lemons", "onions"]
DATES = ["the 3rd", "the 5th", "the 12th", "the 15th", "the 20th", "the 28th", "June 3", "May 14", "March 9", "April 22", "October 1", "the first", "the tenth", "the twentieth"]
PRON_SWAP = [("him", "her"), ("he", "she"), ("his", "her"), ("them", "us"), ("they", "we"), ("this", "that"), ("these", "those"), ("I", "we"), ("me", "him"), ("you", "me"), ("her", "him"), ("she", "he"), ("we", "they"), ("us", "them"), ("that", "this"), ("it", "this")]

KIND = {"day": DAYS, "month": MONTHS, "name": NAMES, "place": PLACES, "time": TIMES, "num": NUMS, "money": MONEY, "color": COLORS, "item": ITEMS, "date": DATES}

TEMPLATES = """
Let's meet at {time} on {day}.
Can we meet {day} at {time}?
I have a dentist appointment on {day} at {time}.
The deadline is {date}, so I need to start soon.
Call {name} before {time} today.
Tell {name} that the meeting is on {day}.
I'm having lunch with {name} on {day}.
The flight leaves on {day} at {time}.
We need {num} chairs for the event at {place}.
Meet me at {place} around {time}.
Send {name} the file before {day}.
The rent is {money} and it's due on {date}.
I paid {money} for the tickets.
It costs about {money} per person.
Order {num} pizzas for the study group.
Bring {num} copies of the handout to {place}.
The party is at {place} on {day}.
I'll pick up {name} from {place} at {time}.
The interview is on {date} at {time}.
She wants the {color} one for her room.
Let's paint the wall {color} this weekend.
I need to buy {item} and {item} on the way home.
Add {item} to the shopping list.
{name} is coming to {place} on {day}.
Ask {name} if {day} works for the review.
Our reservation is for {num} people at {time}.
The conference is in {month} and I still need a hotel.
Remind me to call {name} on {date}.
I'm free {day} after {time}.
The package should get here by {day}.
Schedule the call with {name} for {day} morning.
We're meeting at {place} at {time} to go over the plan.
Pick up {num} boxes from {place} on {day}.
The quiz is on {day}, and the paper is due {date}.
{name} said the budget is {money}.
I'll see {name} at {place} on {day}.
Book the room for {day} at {time}.
Move the standup to {time} on {day}.
Is {day} or {day} better for you?
We can meet on {day} or {day}, whichever works.
Do you want {item} or {item} for dinner?
I can do {time} or {time}, you pick.
Sorry, I can't make it on {day}.
Actually, {day} works better for me.
No, the meeting is on {day}.
There will be {num} people at the dinner on {day}.
Can you drop {name} off at {place}?
""".strip().split("\n")

FALSE_STARTS = [
    "What I wanted to", "The other day I", "And then the", "I was going to", "We could go to the", "I just", "So when I", "She went to the", "Let me", "And the other",
]

NOCOMMA_LIKE_AFTER = {"was", "is", "are", "were", "it's", "that's", "be", "been", "so", "and", "but", "just", "a", "about", "then", "to", "for", "with", "in", "on", "at", "was", "got", "had", "had"}
FILLER_WORDS = ["um", "uh", "er", "erm", "umm", "uhh", "hmm", "ah"]
CUES = [
    "actually no", "actually", "no wait", "wait no", "no no", "no", "I mean", "sorry", "or sorry", "or rather", "or I mean", "scratch that", "wait", "no sorry", "sorry I mean", "um actually", "uh no", "actually sorry", "rather", "oh wait", "no I mean", "well no",
]
# "make that" with a restated value is a plain D correction. It is also an E trigger
# when the value stands alone, so the two forms below differ on restating.
E_CUES = [
    "Actually, make that", "Actually make that", "Make that", "No, make that", "Sorry, make that", "Wait, make that", "Oh, make that", "Actually, make it", "Make it", "No, make it", "Change that to", "Actually, change that to", "Sorry, I meant", "I meant", "Actually, I meant", "No, I meant", "Wait, I meant", "Oh wait, make that", "Scratch that, make it", "Actually, let's make it", "Let's make that", "Or make that",
]

# ------------------------------------------------------------- token helpers


class Tok:
    __slots__ = ("t", "l", "slot", "kind")

    def __init__(self, t, l="K", slot=None, kind=None):
        self.t, self.l, self.slot, self.kind = t, l, slot, kind

    def copy(self):
        return Tok(self.t, self.l, self.slot, self.kind)


def split_punct(w):
    m = re.match(r"^([\"'(\[]*)(.*?)([,.;:?!\"')\]\-]*)$", w)
    return m.group(1), m.group(2), m.group(3)


def ends_term(t):
    return bool(re.search(r"[.?!][\"')]*$", t))


def strip_trail(t):
    return re.sub(r"[,.;:?!]+$", "", t)


def with_comma(t):
    t = strip_trail(t)
    return t + ","


def lower_first(t, lowerable):
    lead, core, trail = split_punct(t)
    if core and core.lower() in lowerable and core[0].isupper():
        return lead + core[0].lower() + core[1:] + trail
    return t


def cap_first(t):
    lead, core, trail = split_punct(t)
    return lead + core[:1].upper() + core[1:] + trail


def toks_from_text(s, slot=None, kind=None):
    return [Tok(w, "K", slot, kind) for w in s.split()]


def expand_template(tpl, rng):
    """Return (tokens, slot_values). Slot tokens carry slot id and kind."""
    toks, vals = [], {}
    for i, part in enumerate(re.split(r"(\{\w+\})", tpl)):
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m:
            kind = m.group(1)
            v = rng.choice(KIND[kind])
            vals[len(vals)] = (kind, v)
            sid = len(vals) - 1
            toks += toks_from_text(v, sid, kind)
        else:
            if toks and part.strip() and re.fullmatch(r"[\s]*[,.;:?!]+[\s]*", part):
                toks[-1].t += part.strip()
            else:
                ws = part.split()
                if toks and ws and part[:1] != " " and re.fullmatch(r"[,.;:?!].*", ws[0]):
                    m = re.match(r"[,.;:?!]+", ws[0])
                    toks[-1].t += m.group(0)
                    ws[0] = ws[0][len(m.group(0)):]
                    ws = [w for w in ws if w]
                toks += [Tok(w, "K") for w in ws]
    # sentence-initial slot values (names etc.) are fine as written
    return toks


def detect_slots(toks):
    """Flag weekday, month, digit and pronoun tokens in free text as slots."""
    sid = 100
    for t in toks:
        _, core, _ = split_punct(t.t)
        if t.slot is not None:
            continue
        if core in DAYS:
            t.slot, t.kind = sid, "day"
        elif core in MONTHS:
            t.slot, t.kind = sid, "month"
        elif re.fullmatch(r"\d{1,3}", core):
            t.slot, t.kind = sid, "num"
        elif core.lower() in ("him", "her", "he", "she", "they", "them", "we", "us", "this", "that"):
            t.slot, t.kind = sid, "pron"
        else:
            continue
        sid += 1


def decoy_for(kind, true_value, rng, vocab):
    """A wrong value of the same kind as true_value (string)."""
    if kind == "pron":
        low = true_value.lower()
        pool = [b for a, b in PRON_SWAP if a.lower() == low] + [a for a, b in PRON_SWAP if b.lower() == low]
        d = rng.choice(pool) if pool else rng.choice(["him", "her", "them", "us"])
        return d.capitalize() if true_value[:1].isupper() else d
    if kind in KIND:
        pool = [x for x in KIND[kind] if x.lower() != true_value.lower()]
        return rng.choice(pool)
    return rng.choice(vocab)


# ------------------------------------------------------------------- ops


class Gen:
    def __init__(self, rng, sentences, lowerable, vocab):
        self.rng, self.sents, self.lowerable, self.vocab = rng, sentences, lowerable, vocab

    # --- shared helpers
    def at_sentence_start(self, toks, pos):
        return pos == 0 or ends_term(toks[pos - 1].t)

    def insert(self, toks, pos, new):
        """Insert D tokens `new` before toks[pos]; fix capitalisation around them."""
        rng = self.rng
        start = self.at_sentence_start(toks, pos)
        if start and new:
            new[0].t = cap_first(new[0].t)
            if pos < len(toks) and toks[pos].l != "E":
                toks[pos].t = lower_first(toks[pos].t, self.lowerable)
        elif new:
            new[0].t = new[0].t[0].lower() + new[0].t[1:] if new[0].t[:1].isupper() and split_punct(new[0].t)[1] not in ("I",) else new[0].t
        toks[pos:pos] = new

    def free_positions(self, toks, allow_end=False):
        ps = []
        for i in range(len(toks) + (1 if allow_end else 0)):
            if i < len(toks) and toks[i].l == "E" and i > 0 and toks[i - 1].l == "E":
                continue
            if 0 < i < len(toks) and toks[i].l == "D" and toks[i - 1].l == "D":
                pass
            ps.append(i)
        return ps

    # --- ops
    def filler(self, toks, kind=None):
        rng = self.rng
        pos = rng.choice(self.free_positions(toks)) if toks else 0
        if kind is None:
            kind = rng.choices(["um", "like", "youknow", "imean"], [60, 20, 12, 8])[0]
        if kind == "um":
            w = rng.choice(FILLER_WORDS)
            style = rng.choices(["c", "n", "e", "cc"], [55, 25, 8, 12])[0]
            t = w + {"c": ",", "n": "", "e": "...", "cc": ","}[style]
            new = [Tok(t, "D")]
            if style == "cc" and pos > 0 and toks[pos - 1].l == "K":
                toks[pos - 1].t = with_comma(toks[pos - 1].t)
        elif kind == "like":
            prev = split_punct(toks[pos - 1].t)[1].lower() if pos > 0 else ""
            comma = not (prev in NOCOMMA_LIKE_AFTER and rng.random() < 0.6) and rng.random() < 0.85
            new = [Tok("like," if comma else "like", "D")]
            if comma and pos > 0 and rng.random() < 0.3 and toks[pos - 1].l == "K" and not ends_term(toks[pos - 1].t):
                toks[pos - 1].t = with_comma(toks[pos - 1].t)
            # never create "like like" or touch real "like"
            for j in (pos - 1, pos):
                if 0 <= j < len(toks) and split_punct(toks[j].t)[1].lower() == "like":
                    return
        elif kind == "youknow":
            new = [Tok("you", "D"), Tok("know," if rng.random() < 0.75 else "know", "D")]
        else:
            new = [Tok("I", "D"), Tok("mean," if rng.random() < 0.8 else "mean", "D")]
        self.insert(toks, pos, new)

    def trailing_filler(self, toks):
        rng = self.rng
        if not toks or toks[-1].l != "K":
            return
        term = re.search(r"[.?!]+$", toks[-1].t)
        p = term.group(0) if term else ""
        toks[-1].t = strip_trail(toks[-1].t) + ","
        if rng.random() < 0.3:
            toks[-1].t = strip_trail(toks[-1].t)
        kind = rng.choice(["you know", "I mean", "um", "like", "uh"])
        parts = kind.split()
        new = [Tok(w, "D") for w in parts]
        new[-1].t += p
        toks.extend(new)

    def repeat(self, toks):
        rng = self.rng
        cands = [i for i, t in enumerate(toks) if t.l == "K"]
        if not cands:
            return
        i = rng.choice(cands)
        k = rng.choices([1, 2, 3], [60, 28, 12])[0]
        seg = toks[i:i + k]
        if any(t.l != "K" for t in seg) or len(seg) < k:
            k, seg = 1, toks[i:i + 1]
        if any(ends_term(t.t) for t in seg[:-1]):
            return
        start = self.at_sentence_start(toks, i)
        copy = [t.copy() for t in seg]
        for t in copy:
            t.l, t.slot, t.kind = "D", None, None
        last = copy[-1]
        lead, core, trail = split_punct(last.t)
        mode = rng.choices(["plain", "comma", "partial", "filler"], [45, 25, 8, 22])[0]
        if mode == "partial" and k == 1 and len(core) > 2:
            copy = [Tok(core[:rng.randint(1, 2)] + "-", "D")]
        else:
            last.t = lead + core + ("," if mode in ("comma", "filler") and rng.random() < 0.7 else "")
            if mode == "filler":
                copy.append(Tok(rng.choice(FILLER_WORDS) + rng.choice([",", ""]), "D"))
        if start:
            copy[0].t = cap_first(copy[0].t)
            toks[i].t = lower_first(toks[i].t, self.lowerable)
        toks[i:i] = copy
        if mode in ("plain", "comma") and rng.random() < 0.15:
            toks[i:i] = [c.copy() for c in copy]  # triple stutter

    def false_start(self, toks):
        rng = self.rng
        if not toks:
            return
        pos = rng.choice([0, 0, 0] + [p for p in range(len(toks)) if self.at_sentence_start(toks, p)])
        if rng.random() < 0.7:
            frag = rng.choice(FALSE_STARTS).split()
        else:
            other = rng.choice(self.sents).split()
            n = rng.randint(2, 5)
            frag = other[:n]
            frag[-1] = strip_trail(frag[-1])
            frag[0] = frag[0]
        end = rng.choice(["", ",", "...", ",", "-"])
        frag[-1] = strip_trail(frag[-1]) + end
        new = [Tok(w, "D") for w in frag]
        self.insert(toks, pos, new)
        if rng.random() < 0.25:
            self.insert(toks, pos + len(new), [Tok(rng.choice(FILLER_WORDS) + ",", "D")])

    def cue_toks(self, rng):
        cue = rng.choice(CUES)
        ws = cue.split()
        style = rng.choices(["c", "n"], [80, 20])[0]
        if style == "c":
            ws[-1] += ","
        return [Tok(w, "D") for w in ws]

    def correct(self, toks):
        """Insert decoy + cue before a slot span, or a random span."""
        rng = self.rng
        slots = {}
        for i, t in enumerate(toks):
            if t.slot is not None and t.l == "K":
                slots.setdefault(t.slot, []).append(i)
        if slots and rng.random() < 0.85:
            sid = rng.choice(list(slots))
            idx = slots[sid]
            i, j = idx[0], idx[-1] + 1
            kind = toks[i].kind
            true_val = " ".join(split_punct(t.t)[1] for t in toks[i:j])
            decoy = decoy_for(kind, true_val, rng, self.vocab)
        else:
            cands = [i for i, t in enumerate(toks) if t.l == "K" and split_punct(t.t)[1].isalpha()]
            if not cands:
                return
            i = rng.choice(cands)
            n = rng.choices([1, 2, 3], [60, 28, 12])[0]
            j = i + n
            if j > len(toks) or any(t.l != "K" or ends_term(t.t) for t in toks[i:j - 1]):
                j = i + 1
            n = j - i
            dw = rng.choice(self.sents).split()
            s0 = rng.randrange(0, max(1, len(dw) - n + 1))
            decoy = " ".join(split_punct(w)[1] for w in dw[s0:s0 + n]) or "thing"
            if toks[i].l != "K":
                return
        # decoy tokens
        dwords = decoy.split()
        if not dwords:
            return
        ctx = 0
        if rng.random() < 0.25 and i > 0:
            ctx = rng.randint(1, min(3, i))
            if any(t.l != "K" or ends_term(t.t) for t in toks[i - ctx:i]):
                ctx = 0
        lead_toks = [Tok(split_punct(t.t)[1], "D") for t in toks[i - ctx:i]]
        dtoks = [Tok(w, "D") for w in dwords]
        if toks[i].t[:1].isupper() and not self.at_sentence_start(toks, i) and toks[i].kind not in ("day", "month", "name"):
            pass
        cue = self.cue_toks(rng)
        # comma after decoy
        if rng.random() < 0.8:
            dtoks[-1].t += ","
        # last kept span token may carry trailing punctuation; keep it there
        pos = i - ctx
        # sentence start capitalisation handled by insert()
        new = lead_toks + dtoks + cue
        if ctx and toks[pos].t[:1].isupper():
            pass
        self.insert(toks, pos, new)
        if ctx:
            # restated context repeats words, keep it lowercased mid-sentence
            k = pos + len(new)
            toks[k].t = lower_first(toks[k].t, self.lowerable)

    def pronoun_chain(self, toks):
        """him, her, her / 5, 6, 6 style chains: X, Y, Y with Y kept."""
        rng = self.rng
        cands = [i for i, t in enumerate(toks) if t.slot is not None and t.kind in ("pron", "num", "day", "name", "color") and t.l == "K"]
        if not cands:
            return
        i = rng.choice(cands)
        kind = toks[i].kind
        true_val = split_punct(toks[i].t)[1]
        d = decoy_for(kind, true_val, rng, self.vocab)
        mode = rng.choice(["a", "b", "c"])
        start = self.at_sentence_start(toks, i)
        sep = rng.choice([",", ""]) if mode != "c" else ","
        first = Tok(d + sep, "D")
        new = [first]
        if mode in ("a", "b"):
            lead, core, trail = split_punct(toks[i].t)
            new.append(Tok(core + (rng.choice([","]) if mode == "a" else ""), "D"))
        if mode == "c":
            new.append(Tok(rng.choice(FILLER_WORDS) + ",", "D"))
        self.insert(toks, i, new)

    def backref(self, toks, sid=None):
        """Build 'S1(decoy value). Actually make that VALUE.' Returns new token list or None."""
        rng = self.rng
        slots = {}
        for i, t in enumerate(toks):
            if t.slot is not None and t.l == "K" and t.kind in KIND:
                slots.setdefault(t.slot, []).append(i)
        if not slots:
            return None
        sid = rng.choice(list(slots))
        idx = slots[sid]
        kind = toks[idx[0]].kind
        true_val = " ".join(split_punct(toks[i].t)[1] for i in idx)
        decoy = decoy_for(kind, true_val, rng, self.vocab)
        # S1 carries the decoy
        lead0, _, _ = split_punct(toks[idx[0]].t)
        trail_last = split_punct(toks[idx[-1]].t)[2]
        dw = decoy.split()
        newv = [Tok(w, "K") for w in dw]
        newv[0].t = lead0 + newv[0].t
        newv[-1].t += trail_last
        out = toks[:idx[0]] + newv + toks[idx[-1] + 1:]
        # attach correction
        tv = true_val.split()
        mode = rng.choices(["sent", "comma", "inline"], [60, 25, 15])[0]
        cue = rng.choice(E_CUES)
        cw = cue.split()
        if kind == "num" and rng.random() < 0.2:
            pass
        corr = [Tok(w, "E") for w in cw] + [Tok(w, "E") for w in tv]
        end_p = rng.choice([".", ".", ""])
        corr[-1].t += end_p
        if mode == "sent":
            if not ends_term(out[-1].t):
                out[-1].t = strip_trail(out[-1].t) + "."
            corr[0].t = cap_first(corr[0].t)
        elif mode == "comma":
            out[-1].t = strip_trail(out[-1].t) + ","
            corr[0].t = corr[0].t[0].lower() + corr[0].t[1:] if corr[0].t.lower() not in ("i",) else corr[0].t
            if not ends_term(out[-1].t) and cw[0].lower() in ("actually,", "no,", "sorry,", "wait,", "oh,"):
                pass
        else:
            # correction placed right after the slot: "meet at 3 on Tuesday, make that Wednesday"
            k = idx[0] + len(dw)
            out[k - 1].t = strip_trail(out[k - 1].t) + ","
            corr[0].t = corr[0].t[0].lower() + corr[0].t[1:]
            corr[-1].t = corr[-1].t.rstrip(".") + ("," if k < len(out) else "")
            out[k:k] = corr
            return out
        return out + corr

    def special_e(self, rng):
        """Hand-made E patterns that need their own shape."""
        pick = rng.choice(["ord", "plural", "appos", "change"])
        if pick == "ord":
            items = rng.sample(ITEMS, 3)
            ordn = rng.choice([("first", 0), ("second", 1), ("third", 2)])
            newi = rng.choice([x for x in ITEMS if x not in items])
            lead = rng.choice(["Get", "Buy", "I need", "Put", "We need", "Pick up"])
            s1 = f"{lead} {items[0]}, {items[1]} and {items[2]}."
            c = rng.choice(["Change the {o} one to {n}.", "Actually, change the {o} one to {n}.", "Make the {o} one {n}.", "Replace the {o} one with {n}.", "Sorry, change the {o} item to {n}."])
            toks = toks_from_text(s1) + [Tok(w, "E") for w in c.format(o=ordn[0], n=newi).split()]
            return toks
        if pick == "plural":
            n = rng.choice(["three", "four", "five", "ten", "2", "6", "12"])
            sing, plur = rng.choice([("box", "boxes"), ("chair", "chairs"), ("ticket", "tickets"), ("table", "tables"), ("bag", "bags"), ("folder", "folders"), ("copy", "copies"), ("page", "pages")])
            lead = rng.choice(["I ordered", "We need", "Please bring", "I'll take", "Grab"])
            tail = rng.choice(["", " for the event", " for Friday", " today"])
            s1 = f"{lead} {n} {sing}{tail}."
            c = rng.choice(["Make that {p}.", "Actually, make that {p}.", "Sorry, make that {p}.", "Oh, make that {p}."]).format(p=plur)
            return toks_from_text(s1) + [Tok(w, "E") for w in c.split()]
        if pick == "appos":
            a, b = rng.choice([("Pat's car", "his truck"), ("Sarah's laptop", "her tablet"), ("Dana's brother", "her cousin"), ("the blue folder", "the green one"), ("Jake's place", "his office"), ("my phone", "my laptop"), ("the first draft", "the final draft")])
            lead = rng.choice(["I saw", "We borrowed", "He mentioned", "I left it at", "She took", "I found"])
            if lead == "I left it at":
                lead2 = "I left it in"
            tail = rng.choice([" yesterday.", " earlier.", " on Monday.", "."])
            s1 = f"{lead} {a}"
            cue = rng.choice(["I mean", "sorry"])
            if cue == "I mean":
                e = (b + " I mean").split()
            else:
                e = ("sorry " + b).split()
            toks = toks_from_text(s1)
            toks[-1].t += ","
            etoks = [Tok(w, "E") for w in e]
            etoks[-1].t += tail.strip() if tail.strip() in (".",) else "."
            return toks + etoks
        # change
        item = rng.choice(["the meeting", "the reservation", "the appointment", "the deadline", "the pickup", "the call"])
        d1, d2 = rng.sample(DAYS, 2)
        s1 = f"I booked {item} for {d1}."
        c = rng.choice(["Change {d} to {e}.", "Actually, move it to {e}.", "Change that to {e}.", "Make it {e} instead.", "Actually, make that {e} instead."])
        c = c.format(d=d1, e=d2)
        return toks_from_text(s1) + [Tok(w, "E") for w in c.split()]


# ------------------------------------------------------------ assemble


def build_segment(g, rng, kind, text_or_tpl):
    if kind == "tpl":
        toks = expand_template(text_or_tpl, rng)
        # sentence-initial: ensure capital
        toks[0].t = cap_first(toks[0].t)
    else:
        toks = toks_from_text(text_or_tpl)
        detect_slots(toks)
    if rng.random() < 0.10 and not ends_term(toks[0].t) or False:
        starter = rng.choice(["Actually,", "No,", "Sorry,", "Wait,", "Well,", "Okay,", "Yeah,", "Hey,", "So,", "Right,", "Look,", "Oh,", "Honestly,", "Sorry about that,", "Okay so", "I'm sorry,", "I'm sorry but", "And then", "So then", "Then"])
        sw = [Tok(w, "K") for w in starter.split()]
        if toks[0].slot is None:
            toks[0].t = lower_first(toks[0].t, g.lowerable)
        toks = sw + toks
    if rng.random() < 0.05 and toks[0].slot is None:
        lead = rng.choice([["You", "know,"], ["I", "mean,"], ["Like,"], ["You", "know,"], ["I", "mean,"]])
        toks[0].t = lower_first(toks[0].t, g.lowerable)
        toks = [Tok(w, "D") for w in lead] + toks
    r = rng.random()
    if r < 0.22:
        return toks  # clean passthrough
    # decide E first (needs fresh slots)
    if rng.random() < (0.3 if kind == "tpl" else 0.17):
        if kind == "tpl" or any(t.slot is not None for t in toks):
            e = g.backref(toks)
            if e:
                toks = e
        elif rng.random() < 0.5:
            toks = g.special_e(rng)
    n_ops = rng.choices([1, 2, 3], [55, 33, 12])[0]
    ops = ["filler", "like", "youknow", "imean", "repeat", "false", "correct", "chain", "trail"]
    wts = [32, 15, 6, 5, 18, 3, 14, 4, 4]
    for _ in range(n_ops):
        op = rng.choices(ops, wts)[0]
        if op in ("filler", "like", "youknow", "imean"):
            g.filler(toks, {"filler": "um", "like": "like", "youknow": "youknow", "imean": "imean"}[op])
        elif op == "repeat":
            g.repeat(toks)
        elif op == "false":
            g.false_start(toks)
        elif op == "correct":
            g.correct(toks)
        elif op == "chain":
            g.pronoun_chain(toks)
        else:
            g.trailing_filler(toks)
    return toks


def flip(rng, lead_sentence=None):
    a, b = rng.choice([("yeah", "no"), ("yes", "no"), ("no", "yes"), ("no", "yeah"), ("sure", "no"), ("yeah", "nope"), ("okay", "no")])
    toks = []
    if rng.random() < 0.5:
        toks.append(Tok(rng.choice(["Um,", "Uh,", "Hmm,", "Well,"]), "D"))
        toks.append(Tok(a + rng.choice([".", ","]), "D"))
    else:
        toks.append(Tok(cap_first(a) + rng.choice([".", ","]), "D"))
    sep = toks[-1].t[-1]
    cue = rng.choice(["Actually", "Wait", "Actually, wait", "Hold on", "No wait", "Actually no"])
    cw = cue.split()
    for w in cw:
        toks.append(Tok(w, "D"))
    toks[-1].t += ","
    toks.append(Tok(b + rng.choice([".", ".", "!"]), "K"))
    if sep == ",":
        toks[-len(cw) - 1].t = toks[-len(cw) - 1].t[:-1] + "."
    return toks


def style(toks, rng):
    r = rng.random()
    if r < 0.72:
        pass
    elif r < 0.82:  # lowercase, keep punctuation
        for t in toks:
            t.t = t.t.lower() if t.t != "I" else "I"
    elif r < 0.90:  # lowercase, no punctuation
        for t in toks:
            t.t = re.sub(r"[,.;:?!]+", "", t.t.lower())
    else:  # drop final punctuation
        toks[-1].t = toks[-1].t.rstrip(".?!") or toks[-1].t
    out = [t for t in toks if t.t.strip()]
    return out


XCUES = ["Actually, no,", "Wait, no,", "Sorry,", "No, wait,", "I mean", "Actually,", "No,", "Sorry, I mean", "Oh wait,", "No no no,", "Actually, sorry,"]


def xsent_example(g, rng, pools):
    """Cross-sentence self-correction: 'X. Actually, no, Y.' (D) or a real new thought (all K)."""
    src = rng.choices(["vault", "gen", "tpl"], [0.35, 0.1, 0.55])[0]
    item = rng.choice(pools[src])
    if src == "tpl":
        toks = expand_template(item, rng)
        toks[0].t = cap_first(toks[0].t)
    else:
        toks = toks_from_text(item)
        detect_slots(toks)
    cue = rng.choice(XCUES)
    cw = cue.split()
    if rng.random() < 0.3:  # negative: a real new thought, nothing deleted
        if not ends_term(toks[-1].t):
            toks[-1].t = strip_trail(toks[-1].t) + "."
        other = rng.choice(pools["vault"] + pools["gen"]).split()
        other[0] = lower_first(other[0], g.lowerable)
        if other[0] in ("I",):
            pass
        c = [Tok(w, "K") for w in cw] + [Tok(w, "K") for w in other]
        if cw[-1].endswith(",") is False and cw[-1] not in ("mean",):
            c[len(cw) - 1].t += ","
        if rng.random() < 0.4:
            g.filler(toks)
        return style(toks + c, rng)
    ids = [i for i, t in enumerate(toks) if t.slot is not None and (t.kind in KIND or t.kind == "pron")]
    if not ids:
        return None
    sid = toks[ids[-1]].slot
    idx = [i for i in ids if toks[i].slot == sid]
    i, j = idx[0], idx[-1] + 1
    if i < 2:
        return None
    kind = toks[i].kind
    true_val = " ".join(split_punct(t.t)[1] for t in toks[i:j])
    decoy = decoy_for(kind, true_val, rng, g.vocab)
    pre = toks[:i]
    for t in pre:
        t.slot = t.kind = None
    if rng.random() < 0.55:
        g.filler(pre)
    x = [Tok(w, "D") for w in decoy.split()]
    if not x[-1].t.endswith("."):
        x[-1].t += "."
    y = [t for t in toks[i:j]]
    for t in y:
        t.slot = t.kind = None
        t.l = "K"
    y[-1].t = strip_trail(y[-1].t) + rng.choice([".", ".", ""])
    if cw[-1].endswith(",") is False and cw[-1] != "mean" and rng.random() < 0.8:
        cw = cw[:-1] + [cw[-1] + ","]
    c = [Tok(w, "D") for w in cw]
    if kind == "pron" or rng.random() < 0.5:
        y[0].t = y[0].t
    return style(pre + x + c + y, rng)


def make_example(g, rng, pools):
    if rng.random() < 0.10:
        for _ in range(5):
            ex = xsent_example(g, rng, pools)
            if ex:
                return {"words": [t.t for t in ex], "labels": [t.l for t in ex]}
    nseg = rng.choices([1, 2, 3], [55, 33, 12])[0]
    toks, words = [], 0
    for _ in range(nseg):
        src = rng.choices(["vault", "gen", "tpl"], [0.5, 0.2, 0.3])[0]
        pool = pools[src]
        item = rng.choice(pool)
        kind = "tpl" if src == "tpl" else "txt"
        if rng.random() < 0.025:
            seg = flip(rng)
        else:
            seg = build_segment(g, rng, kind, item)
        if toks and not ends_term(toks[-1].t):
            toks[-1].t = strip_trail(toks[-1].t) + "."
        toks += seg
        if len(toks) > 70:
            break
    toks = style(toks, rng)
    if any(t.l == "K" or t.l == "E" for t in toks) is False:
        return None
    return {"words": [t.t for t in toks], "labels": [t.l for t in toks]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--dev", type=int, default=2500)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    sents = vault_sentences()
    os.makedirs(ROOT + "/data/raw", exist_ok=True)
    with open(ROOT + "/data/raw/vault_sentences.txt", "w") as f:
        f.write("\n".join(sents))
    rng.shuffle(sents)
    # hold out vault sentences and generic sentences for dev
    nd = max(50, len(sents) // 15)
    v_dev, v_tr = sents[:nd], sents[nd:]
    gen = [s for s in GENERIC if s.strip()]
    rng.shuffle(gen)
    g_dev, g_tr = gen[:25], gen[25:]
    tpls = [t for t in TEMPLATES if t.strip()]

    # vocabulary
    cnt_low, cnt_cap = Counter(), Counter()
    for s in sents + gen:
        ws = s.split()
        for k, w in enumerate(ws):
            c = split_punct(w)[1]
            if not c.isalpha():
                continue
            (cnt_cap if c[0].isupper() else cnt_low)[c.lower()] += 1
    lowerable = {w for w in cnt_low if cnt_low[w] > 0 and cnt_cap[w] <= cnt_low[w] * 3 and w not in ("i",)}
    lowerable -= {d.lower() for d in DAYS + MONTHS + NAMES}
    vocab = [w for w, c in cnt_low.items() if c >= 3 and 3 <= len(w) <= 9 and w.isalpha()]

    def run(sentences_v, sentences_g, n, seed):
        r = random.Random(seed)
        g = Gen(r, sentences_v + sentences_g + GENERIC, lowerable, vocab)
        # No notes folder: the "vault" pool falls back to the hand-written sentences.
        pools = {"vault": sentences_v or sentences_g, "gen": sentences_g, "tpl": tpls}
        out = []
        while len(out) < n:
            ex = make_example(g, r, pools)
            if ex and len(ex["words"]) >= 2:
                out.append(ex)
        return out

    train = run(v_tr, g_tr, a.n, a.seed)
    dev = run(v_dev, g_dev, a.dev, a.seed + 1)
    os.makedirs(ROOT + "/data", exist_ok=True)
    for name, rows in (("tagger_train", train), ("tagger_dev", dev)):
        with open(f"{ROOT}/data/{name}.jsonl", "w") as f:
            for ex in rows:
                f.write(json.dumps(ex) + "\n")
    lab = Counter(l for ex in train for l in ex["labels"])
    print(f"vault sentences {len(sents)}, generic {len(gen)}, templates {len(tpls)}")
    print(f"train {len(train)} dev {len(dev)} labels {dict(lab)}")
    for ex in rng.sample(train, 12):
        print(" ".join(f"{w}/{l}" if l != "K" else w for w, l in zip(ex["words"], ex["labels"])))


if __name__ == "__main__":
    main()
