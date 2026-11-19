"""Synthetic workload: requests built to a shape you choose — input length (ISL), output length
(OSL), how much of the input is a shared prefix (cache hit target), and single or multi-turn.

Text is generated from a fixed English word list with the run's seed, so both providers get the
same bytes. Token counts are targets: the prompt is sized at ~1.10 tokens per word and the measured
prompt/completion tokens from each provider's `usage` are what the results report.

    turn 0      [shared prefix: cache_hit × ISL]  [unique: rest of ISL]  "continue, ~OSL words"
    turn t > 0  previous turns + a fixed assistant reply of ~OSL words + [unique: turn ISL] + same ask

The shared prefix is the same in every session of the run (and opens with the run nonce), so after
the first session warms it, every first turn can hit it; later turns can also hit their own history.
Nothing is graded for correctness: a reply fails only on transport errors or if it stops at less
than half the requested OSL.
"""
from .base import Suite, Task, Verdict
from ..failures import check_output

TOKENS_PER_WORD = 1.10          # measured 19 Nov 2026: Gemma 4 1.09, GLM-5.3 Flash 1.12 (incl. template)
WORDS = ("the of and to in is that for it as was with be by on not he this are or his from at which "
         "but have an they you were her she there been one all we their has would when if more no "
         "out so said what up its about into than them can only other new some could time these two may "
         "then do first any my now such like our over man me even most made after also did many before "
         "must through back years where much your way well down should because each just those people "
         "how too little state good very make world still own see men work long get here between both "
         "life being under never day same another know while last might us great old year off come "
         "since against go came right used take three himself few house use during without again place "
         "around however home small found thought went say part once general high upon school every "
         "does got united left number course war until always away something fact though water less "
         "public put think almost hand enough far took head yet government system better set told "
         "nothing night end why called didn find look asked later point knew city next program business "
         "give group toward young days let room president side social given present several order "
         "national possible rather second face per among form important often things looked early white "
         "case become large big need four within felt along children saw best church ever least power "
         "development light thing seemed family interest want members mind country area others done "
         "turned although open god service certain kind problem began different door thus help sense "
         "means whole matter perhaps itself york times law human line above name example action company "
         "hands local show whether five history gave today either act feet across taken past quite "
         "anything seen having death experience body word half really week field am car words already "
         "information tell college shall together money period held keep sure probably free seems real "
         "behind cannot miss political air question making office brought whose special heard major "
         "problems ago became federal moment study available known result street economic boy position "
         "reason change south board individual job society areas west close turn love community true "
         "court force full seem am wife future age wanted department voice center woman control common "
         "necessary following front sometimes six girl clear further land run students provide feel "
         "party able mother music education university child effect level stood military town short "
         "morning total outside rate figure class art century washington north usually plan leave "
         "therefore evidence top million sound black strong hard various says believe type value play "
         "surface soon mean near lines table peace modern tax road red book personal process situation "
         "minutes increase schools idea english alone women gone nor living months america started "
         "longer dr cut finally third secretary nature private section greater call fire expected").split()
ASK = ("\n\nContinue the text above with about %d words of new prose. Keep writing until you reach that "
       "length; do not summarise, do not stop early.")
SYSTEM = "You are a writing assistant. Continue texts in the same style."


def words(rng, tokens):
    n = max(1, int(tokens / TOKENS_PER_WORD))
    out, sent = [], 0
    for _ in range(n):
        w = rng.choice(WORDS)
        out.append(w.capitalize() if sent == 0 else w)
        sent += 1
        if sent >= rng.randint(9, 16):
            out[-1] += "."
            sent = 0
    return " ".join(out) + "."


class SyntheticSuite(Suite):
    name = "synthetic"
    title = "Synthetic workload"
    scenario = "synthetic"
    graded = False
    entropy_ratio = 10.0             # prompts use a small word list, so fluent continuations compress ~6x
    length_stop_expected = True      # max_tokens = OSL: stopping there is the goal
    degen_signal = "low"
    degen_note = ("Synthetic prompt: random words from a fixed list, asking the model to continue them. "
                  "Loops, word salad and repetitive text are expected here and say little about the "
                  "provider. Check flags from trace replay or quality runs first.")
    blurb = "Requests shaped to your ISL, OSL, cache-hit target and turn count; measures speed and cost, not answers."
    source = ""
    method = [
        'Seeded random words from a fixed list, identical for both providers.',
        'Shared prefix = cache-hit target × ISL.',
        'max_tokens = OSL. Multi-turn re-sends the history.',
        'The first session runs alone to warm the cache.',
        'Not graded. Results show measured ISL, OSL and cache.',
        'No degeneration detection: random prompts make loops expected.',
    ]
    default_n = 20
    warm_first = True
    est_output_tokens = 256

    def params(self):
        return [
            {"key": "n", "label": "Sessions", "type": "int", "default": 20, "min": 1, "max": 2000,
             "help": "Independent conversations (each is one request when turns = 1)."},
            {"key": "isl", "label": "Input tokens (ISL)", "type": "int", "default": 2000, "min": 16, "max": 1000000,
             "help": "Prompt size of the first turn."},
            {"key": "osl", "label": "Output tokens (OSL)", "type": "int", "default": 256, "min": 1, "max": 131072,
             "help": "max_tokens; the prompt asks for at least this much."},
            {"key": "cache_hit", "label": "Cache hit target", "type": "float", "default": 0.0, "min": 0, "max": 0.95,
             "step": 0.05, "help": "Share of the first-turn prompt that is a prefix shared by all sessions."},
            {"key": "turns", "label": "Turns", "type": "int", "default": 1, "min": 1, "max": 50,
             "help": "1 = single request; more = a conversation that re-sends its history."},
            {"key": "turn_isl", "label": "New input per later turn", "type": "int", "default": 200, "min": 1, "max": 200000,
             "help": "Tokens of new user text added on each turn after the first."},
        ]

    def load(self, rng, n, **params):
        p = dict(self.defaults(), **params)
        isl, osl, turns = int(p["isl"]), int(p["osl"]), int(p["turns"])
        hit, turn_isl = float(p["cache_hit"]), int(p["turn_isl"])
        shared_rng = rng.__class__(rng.random())
        shared = words(shared_rng, isl * hit) if hit > 0 else ""
        ask = ASK % max(1, int(osl / TOKENS_PER_WORD))
        tasks = []
        for s in range(n):
            group = "syn/%d" % s
            unique = words(rng, max(1, isl - isl * hit))
            msgs = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": (shared + "\n\n" if shared else "") + unique + ask}]
            label = "ISL %d · OSL %d · %d%% shared%s" % (isl, osl, round(hit * 100), " · %d turns" % turns if turns > 1 else "")
            for t in range(turns):
                if t:
                    msgs = msgs + [{"role": "assistant", "content": words(rng, osl)},
                                   {"role": "user", "content": words(rng, turn_isl) + ask}]
                tasks.append(Task(id="%s/t%d" % (group, t), suite=self.name, messages=msgs, max_tokens=osl,
                                  expect={"osl": osl}, meta={"label": label, "session": s, "est_output": osl,
                                                             "isl_target": isl + t * (osl + turn_isl)},
                                  group=group, turn=t))
        return tasks

    def grade(self, task, reply):
        codes = [c for c in check_output(reply) if c != "truncated"]   # hitting max_tokens is the goal
        got = reply.get("completion_tokens") or 0
        if reply.get("finish_reason") != "length" and got < 0.5 * task.expect["osl"]:
            codes.append("short_output")
        return Verdict("fail" if codes else "pass", codes, answer="%d output tokens" % got,
                       expected="%d" % task.expect["osl"],
                       detail="stopped at %d of %d tokens" % (got, task.expect["osl"]) if codes else "")

    def preview(self, tasks):
        rows = super().preview(tasks)
        by_group = {}
        for t in tasks:
            by_group.setdefault(t.group, []).append(t)
        for r in rows:
            r["prompt_tokens"] = [t.meta["isl_target"] for t in sorted(by_group[r["id"]], key=lambda t: t.turn)]
        return rows
