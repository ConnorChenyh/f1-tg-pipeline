#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.images import render_digest_summary_card, render_item_card


SOURCE_URL = "https://www.formula1.com/en/latest/article/from-smooth-operator-to-the-professor-why-drivers-have-these-nicknames.3wmUKUjTl4uaPdUnXnhbFI"

# Each card translates one original section so the full article remains legible on mobile.
ITEMS = [
    {
        "ordinal": "一",
        "headline": "Checo｜从常见昵称到“墨西哥国防部长”",
        "content": "对凯迪拉克车手塞尔希奥·佩雷兹而言，Checo 的由来其实很朴素：在他的祖国墨西哥，它是 Sergio 的常见简称，类似英语中 James 会被叫作 Jim。\n\n他在红牛效力期间还一度被称为“墨西哥国防部长”。2021 年阿布扎比收官战，年度车手冠军仍有悬念，佩雷兹顽强阻挡当时效力梅赛德斯的汉密尔顿，让队友维斯塔潘得以缩小差距，也为荷兰人赢得首冠铺路。",
    },
    {
        "ordinal": "二",
        "headline": "Albono｜赛车服上的血型，变成朋友间的叫法",
        "content": "阿历山大·阿尔本的昵称可以追溯到赛车生涯初期。当时车手必须把血型清楚标在赛车服上，以便医疗紧急情况处理；他的标识写作“A. Albon O+”，很容易被读成“Albono”。\n\n2020 年第一次封城期间，阿尔本与勒克莱尔、诺里斯、拉塞尔一起直播电竞，这个读起来颇顺口的标识逐渐成了车手朋友间带亲切意味的称呼。",
    },
    {
        "ordinal": "三",
        "headline": "Smooth Operator｜塞恩斯唱出来的电台名场面",
        "content": "塞恩斯无意间给自己取了“Smooth Operator”这个外号。2019 年，他在迈凯伦状态正佳；匈牙利大奖赛取得当季第二次前五后，他给比赛工程师唱起了 Sade 于 1984 年发行的同名歌曲。\n\n塞恩斯是在银石听到这首歌后才跟着唱，却并不知道它很有名。那段电台后来又在巴西大奖赛重现，成为赛季最令人难忘的瞬间之一，车队后来还得向他解释歌曲的真正含义。",
    },
    {
        "ordinal": "四",
        "headline": "Honey Badger｜里卡多的赛道“另一面”",
        "content": "丹尼尔·里卡多 2024 年离开 F1，但“Honey Badger（蜜獾）”这个称呼并未被遗忘。他在围场里精力充沛、常挂着笑容，是极具存在感的人物；进入比赛后又是凶悍的竞争者，因此被比作以强悍和攻击性闻名的蜜獾。\n\n里卡多曾解释：蜜獾可爱、讨喜、外形也很好看，但一旦有人夺走属于它们的东西，就会反击；那就是自己坐进赛车后表现出来的“另一个自我”。",
    },
    {
        "ordinal": "五",
        "headline": "Inspector Seb｜封闭停车区里的“维特尔探长”",
        "content": "四届世界冠军维特尔的雄心和狠劲成就了他的职业生涯，但他的聪明同样重要。他在封闭停车区经常仔细研究身边的赛车，沿着竞争对手的赛车来回查看，希望看清每一处复杂的技术细节。\n\n这并不是特别低调的习惯，于是围场给了他一个带玩笑意味的外号：Inspector Seb，意为“塞布探长”。",
    },
    {
        "ordinal": "六",
        "headline": "The Iceman｜莱科宁的冷静，延续到赛道外",
        "content": "在切尔西出现“冷酷的帕尔默”之前，F1 早已有基米·莱科宁这位“The Iceman（冰人）”。这个外号由迈凯伦时期的 Ron Dennis 所取，芬兰人此后一直带着它。\n\n无论是在赛道上，还是面对媒体工作，莱科宁始终给人冷静、淡然的印象；这份近乎漫不经心的气质，也让“冰人”成为他最深入人心的标签。",
    },
    {
        "ordinal": "七",
        "headline": "Britney｜罗斯伯格最想摆脱的调侃",
        "content": "并非每位车手都乐于拥有像“冰人”这样的称号。罗斯伯格 2006 年初到威廉姆斯围场时，金发和年轻的外形让队友马克·韦伯在与工程师交谈时把他比作流行歌手布兰妮·斯皮尔斯，于是“Britney”出现了。\n\n该赛季巴西收官战，两人发生纠缠，罗斯伯格在回到维修区途中撞车；韦伯在电台里打趣道：“Britney 撞墙了。”到罗斯伯格 2016 年赢得车手冠军时，这个外号大概已让他庆幸地淡出了视野。",
    },
    {
        "ordinal": "八",
        "headline": "The Flying Finn｜哈基宁的速度与沉着",
        "content": "与同胞莱科宁相似，米卡·哈基宁在 F1 的那些年话也不多。他在驾驶舱里看起来很难被扰乱，却从第一次坐进赛车起就展现出始终全力推进的渴望。\n\n在另一位芬兰人凯凯·罗斯伯格的指导下，哈基宁成为舒马赫最强劲的对手之一，并在 1998、1999 年连续夺冠。驾驶时的平静与专注，加上非凡速度和 20 场大奖赛胜利，为他赢得“The Flying Finn（飞翔的芬兰人）”之名。",
    },
    {
        "ordinal": "九",
        "headline": "Il Leone｜意大利车迷心中的“雄狮”曼塞尔",
        "content": "曼塞尔在赛道上的纯粹攻击性与激情很快赢得车迷喜爱，1989 年加盟法拉利后，这份热情更加强烈。他代表这支传奇车队的首秀便在巴西获胜，之后仍不断把赛车逼近极限。\n\n匈牙利站，他从第 12 位一路冲到第一，完成令人屏息的胜利。意大利 tifosi 喜爱他的坚韧，于是把新英雄称为 Il Leone，即“雄狮”。",
    },
    {
        "ordinal": "十",
        "headline": "The Professor｜普罗斯特以理性取胜",
        "content": "阿兰·普罗斯特当然拥有纯粹天赋，但让他成为难以对付的对手的，是他逻辑清晰、善于思考的比赛方式。面对与塞纳之间激烈的长期竞争，他尤其需要这种头脑。\n\n这种有条不紊的方法最终为他带来四次车手世界冠军，也让“The Professor（教授）”成为对他极具敬意的称呼。",
    },
    {
        "ordinal": "十一",
        "headline": "The Rat｜劳达最不体面的标签",
        "content": "劳达同样是极受敬重的车手，但“The Rat（老鼠）”并不能很好体现这一点。最初因为牙齿，他得到的是稍微温和些的“The Mouse（老鼠）”；后来又出现了“King Rat”“Super Rat”等变体。\n\n也有人注意到他在驾驶舱中系统化、略显疏离的气质，称他“The Computer（计算机）”。不过最终最流传下来的，仍然是“The Rat”。",
    },
    {
        "ordinal": "十二",
        "headline": "Hunt the Shunt｜亨特的碰撞年代",
        "content": "詹姆斯·亨特的外号相当直白。在真正迎来成功之前，他很容易卷入夸张的碰撞和事故；从不轻易退让的他，有一段时间为了继续比赛而不断毁掉赛车。\n\n随着岁月推移，他逐步收敛了这一面，并最终在 1976 年获得车手世界冠军。不过“Hunt the Shunt”仍精准保留了他早年比赛方式中的冒险与混乱。",
    },
    {
        "ordinal": "十三",
        "headline": "Mr Monaco｜格拉汉姆·希尔与蒙特卡洛",
        "content": "修剪整齐的胡须和简短的谈吐，让格拉汉姆·希尔成为 1960 年代“绅士车手”的典型，也很适合被称为“Mr Monaco（摩纳哥先生）”。但让他与这座公国永远相连的，不只有形象。\n\n经过六年尝试，他 1963 年首次登上摩纳哥最高领奖台，随后又在 1964、1965、1968、1969 年四次夺冠。塞纳后来以六胜打破他的纪录，但希尔仍是最初的“摩纳哥先生”。",
    },
    {
        "ordinal": "十四",
        "headline": "Black Jack｜不是赌徒的布拉汉姆",
        "content": "“Black Jack”或许会让人以为杰克·布拉汉姆是轻率的赌徒，事实恰恰相反。他是一位理性、强硬的车手，只需跑上几圈，就拥有找出赛车问题的出色能力。\n\n这个称呼来自他的深色头发和神秘气质。布拉汉姆至今仍是历史上唯一一位驾驶以自己名字命名的赛车赢得世界冠军的人。",
    },
    {
        "ordinal": "十五",
        "headline": "El Maestro｜方吉奥为何被称作“大师”",
        "content": "胡安·曼努埃尔·方吉奥至今仍是这项运动最伟大的传奇之一。“El Maestro（大师）”这个称呼尤其得到他的重要拥趸、也是队友的斯特林·莫斯认可。\n\n五个车手世界冠军，加上 51 场大奖赛中 48 次前排发车的耀眼成绩，足以说明这位阿根廷车手为何堪称精通此道的大师。他也在 F1 的早期岁月中留下了长期难以撼动的地位。",
    },
]

INTRO = {
    "ordinal": "序",
    "headline": "为什么 F1 车手总有外号？",
    "content": "外号有的亲切、有的荒唐，也有的近乎冒犯；但它们总能立刻让人想起某一位车手，并成为其 F1 生涯记忆的一部分。\n\n从卡洛斯·塞恩斯的 Smooth Operator，到塞尔希奥·佩雷兹的 Checo，Formula 1 官方本文逐一回顾了 15 位车手外号的由来。",
}


def main() -> int:
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    image_cfg = config.get("images", {})
    width = int(image_cfg.get("width", 1080))
    height = int(image_cfg.get("height", 1440))
    generated_at = datetime.now().astimezone()
    output_dir = ROOT / "output" / f"{generated_at.strftime('%Y-%m-%d_%H%M%S')}_special_nicknames_full"
    draft_dir = output_dir / "drafts" / "digest"
    images_dir = draft_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    cover = render_digest_summary_card(
        [
            {"ordinal": "01-03", "headline": "现役车手：Checo、Albono、Smooth Operator"},
            {"ordinal": "04-06", "headline": "个性标签：Honey Badger、Inspector Seb、Iceman"},
            {"ordinal": "07-09", "headline": "调侃与致敬：Britney、Flying Finn、Il Leone"},
            {"ordinal": "10-12", "headline": "风格与事故：Professor、Rat、Hunt the Shunt"},
            {"ordinal": "13-15", "headline": "三位传奇：Mr Monaco、Black Jack、El Maestro"},
        ],
        width,
        height,
        brand_label="F1 SPECIAL",
        heading="车手外号的来历",
        footer_label="Formula 1 官方全文翻译",
    )
    cover.save(images_dir / "cover.png", format="PNG")

    all_cards = [INTRO, *ITEMS]
    for index, item in enumerate(all_cards, start=1):
        card = render_item_card(
            item["ordinal"],
            item["headline"],
            item["content"],
            width,
            height,
            brand_label="F1 SPECIAL",
            footer_label="车手外号的来历｜全文翻译",
        )
        card.save(images_dir / f"slide_{index:02d}.png", format="PNG")

    draft = {
        "title": "F1 专题：车手外号的来历",
        "telegram_title": "F1专题｜车手外号的来历（全文翻译）",
        "items": all_cards,
        "sources": [SOURCE_URL],
        "risk_note": "内容仅根据 Formula 1 官方原文翻译。",
    }
    (draft_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    (draft_dir / "meta.json").write_text(
        json.dumps({"run_context": {"generated_at": generated_at.isoformat()}, "source_url": SOURCE_URL}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
