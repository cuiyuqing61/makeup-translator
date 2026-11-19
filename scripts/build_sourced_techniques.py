"""Build the public-reference technique library from reviewed source mappings.

The Chinese instructions are original summaries of the cited public tutorials.
Source attribution is not a claim of professional review or endorsement.
"""

from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parents[1] / 'data' / 'knowledge_cards.json'
CHECKED = '2026-10-08'
LOREAL = '欧莱雅巴黎官网 Beauty Magazine'
MAOGEPING = '毛戈平形象设计艺术学校「彩妆资讯」'

SOURCES = {
    'foundation': (LOREAL, 'A Foolproof Guide on How to Apply Foundation', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/how-to-apply-foundation-the-best-way'),
    'concealer': (LOREAL, 'Where to Put Concealer to Brighten, Lift, and Perfect Your Look', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/where-to-apply-concealer-on-your-face'),
    'powder': (LOREAL, 'How To Use Setting Powder for a Long-Lasting Makeup Look', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/how-to-use-setting-powder'),
    'brow': (LOREAL, 'Try This Eyebrow Tutorial for Your Best Arches Yet', 'https://www.lorealparisusa.com/beauty-magazine/makeup/makeup-looks/eyebrow-tutorial-for-beginners'),
    'mgp_brow': (MAOGEPING, '别小看了眉妆的重要性，这样画眉毛颜值翻倍', 'https://www.maogp.com/news_nr/index/730/39300.html'),
    'eye': (LOREAL, 'How to Apply Eye Shadow: Avoid These Common Mistakes', 'https://www.lorealparisusa.com/beauty-magazine/makeup/eye-makeup/beginners-eye-shadow-tutorial'),
    'eye_order': (LOREAL, 'How to Apply Eye Makeup: Foolproof Tips and Tutorials', 'https://www.lorealparisusa.com/beauty-magazine/makeup/eye-makeup/how-to-apply-eye-makeup'),
    'mgp_eye': (MAOGEPING, '初学者基础眼妆教程，每一步都很简单好学', 'https://www.maogp.com/news_nr/index/547/41283.html'),
    'mascara': (LOREAL, 'Mascara Cocktailing: How to Layer Your Mascara', 'https://www.lorealparisusa.com/beauty-magazine/makeup/eye-makeup/how-to-layer-mascara'),
    'blush': (LOREAL, 'Where to Apply Blush So It Looks Symmetrical Every Time', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/how-to-apply-blush-symmetrically'),
    'mgp_blush': (MAOGEPING, '新手化妆万用公式，快进来抄作业（下）', 'https://www.maogp.com/news_nr/index/730/41888.html'),
    'contour': (LOREAL, 'How to Contour Your Face Like a Pro', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/how-to-contour-like-a-pro-makeup-artist'),
    'nose_contour': (LOREAL, 'How to Contour Your Nose Like a Pro', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/nose-contour'),
    'highlight': (LOREAL, 'Where to Apply Highlighter for a Glowy, Lit-From-Within Look', 'https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/where-to-apply-highlighter-how-to'),
    'lip': (LOREAL, 'How to Apply Lipstick: Tips, Tricks, and Techniques to Try', 'https://www.lorealparisusa.com/beauty-magazine/makeup/lip-makeup/how-to-apply-lipstick'),
    'lip_liner': (LOREAL, 'How to Use Lip Liner: A Beginner’s Guide', 'https://www.lorealparisusa.com/beauty-magazine/makeup/lip-makeup/how-to-use-lip-liner'),
    'lip_gloss': (LOREAL, 'The Ultimate Guide to Lip Gloss', 'https://www.lorealparisusa.com/beauty-magazine/makeup/lip-makeup/lip-gloss-ultimate-guide'),
    'mgp_lip': (MAOGEPING, '新手化妆万用公式，快进来抄作业（下）', 'https://www.maogp.com/news_nr/index/730/41888.html'),
}

cards: list[dict] = []


def card(id: str, title: str, region: str, category: str, source: str, section: str,
         actions: list[tuple[str, str]], *, effects: list[str], suitable: list[str],
         tools: list[str], shortcut: str, adaptation: list[str], minutes: int = 3,
         difficulty: str = 'beginner', avoid: list[str] | None = None,
         kinds: list[str] | None = None,
         categories: list[str] | None = None) -> None:
    publisher, article, url = SOURCES[source]
    cards.append({
        'id': id, 'title': title, 'region': region, 'effects': effects,
        'suitable_for': suitable, 'avoid_when': avoid or [], 'tools': tools,
        'product_categories': categories or [category], 'required_product_kinds': kinds or [],
        'actions': [
            {'order': index, 'instruction': instruction, 'success_signal': signal}
            for index, (instruction, signal) in enumerate(actions, 1)
        ],
        'beginner_shortcut': shortcut, 'adaptation_rules': adaptation,
        'source_type': 'public_reference',
        'source_note': '依据所列官网公开文章的指定章节重新编写；位置与用量为操作性归纳，未经专业化妆师复核或用户跟练验证。',
        'source_publisher': publisher, 'source_title': article,
        'source_url': url, 'source_section': section,
        'source_checked_at': CHECKED, 'review_status': 'draft',
        'duration_minutes': minutes, 'difficulty': difficulty,
    })


# Foundation, concealer and setting: separate techniques by purpose and tool.
card('complexion-sponge-foundation', '海绵轻拍液体底妆', 'complexion', 'base', 'foundation',
     'HOW TO APPLY FOUNDATION WITH A MAKEUP SPONGE / STEP #1–3',
     [('先确认海绵清洁，再取少量液体粉底到海绵上。', '海绵表面只有薄薄一层产品。'),
      ('用滚动与轻拍动作把底妆铺匀，鼻翼等窄处改用尖端。', '面颊与鼻翼衔接，未留下海绵印。')],
     effects=['自然', '轻薄', 'satin'], suitable=['日常', 'beginner'], tools=['干净的化妆海绵'],
     shortcut='少量粉底放在干净海绵上，先拍面中，再用尖端拍鼻翼；不够再补。',
     adaptation=['想保留皮肤纹理时先停在一层；需要更多遮盖时仅局部叠加。'], minutes=4,
     kinds=['foundation'])
card('complexion-brush-foundation', '刷具均匀铺开底妆', 'complexion', 'base', 'foundation',
     'HOW TO APPLY FOUNDATION WITH A BRUSH / STEP #1–3',
     [('使用清洁粉底刷，少量取液体粉底。', '刷毛不结成厚团。'),
      ('以短而向下的笔触铺开，再把相邻区域的接缝刷匀。', '没有明显刷痕或粉底边界。')],
     effects=['均匀', '遮盖', 'matte'], suitable=['日常', 'intermediate'], tools=['干净的粉底刷'],
     shortcut='从面中开始用短笔触薄刷，接缝处用刷子余量带匀。',
     adaptation=['刷痕明显时换干净海绵轻拍边界。'], minutes=4, difficulty='intermediate',
     kinds=['foundation'])
card('complexion-spot-conceal', '局部遮盖色斑或泛红', 'complexion', 'base', 'concealer',
     'Where to Put Concealer / Carefully Cover Blemishes',
     [('用干净小刷取接近肤色的遮瑕，仅点在想修饰的位置。', '遮瑕范围不明显大于目标区域。'),
      ('轻拍边缘与底妆衔接，中心保留遮盖。', '远看颜色过渡自然，局部未堆成厚块。')],
     effects=['局部遮瑕', '均匀'], suitable=['日常', 'intermediate'], tools=['干净的遮瑕刷'],
     shortcut='遮瑕只点目标处，轻拍外圈，不反复抹掉中心。',
     adaptation=['颜色校正只在用户已有相应产品且自行确认色调时采用；不对皮肤状况作诊断。'],
     avoid=['局部皮肤破损或明显刺激'], minutes=3, kinds=['concealer'])
card('complexion-under-eye-conceal', '眼下少量提亮遮瑕', 'complexion', 'base', 'concealer',
     'Where Do You Put Concealer for a Natural Makeup Look?',
     [('在确有暗沉的眼下少量点放适合肤色的遮瑕。', '产品局限在眼下，不铺满整个面中。'),
      ('用干净指腹或海绵轻拍开边缘。', '眼下颜色衔接，表面没有厚重积线。')],
     effects=['提亮', '自然'], suitable=['日常', '清透'], tools=['干净指腹或海绵'],
     shortcut='眼下只点少量，用指腹轻拍边缘；够用就停。',
     adaptation=['眼下干燥或易积线时进一步减量；不依据照片判断黑眼圈成因。'],
     avoid=['眼周明显不适'], minutes=2, kinds=['concealer'])
card('complexion-tzone-setting', 'T 区轻量定妆', 'complexion', 'base', 'powder',
     'How To Apply Setting Powder / Apply your setting powder; Remove excess product',
     [('用蓬松刷或粉扑取少量散粉，先抖去明显余粉。', '刷具上不见厚粉团。'),
      ('轻扫容易出油的额头、鼻部与下巴，再用干净刷带走多余粉末。', 'T 区光泽变柔和，脸颊仍保留原有质感。')],
     effects=['定妆', '哑光', '控油'], suitable=['日常', '快速'], tools=['蓬松散粉刷或粉扑'],
     shortcut='只在会出油的 T 区扫薄薄一层，干燥处可跳过。',
     adaptation=['干燥肤感时只处理需要定妆的位置，不全脸加粉。'], minutes=2,
     kinds=['setting_powder'])

# Brows: published beginner sequence plus the school's natural brow-front method.
card('eyebrow-fill-gaps', '沿毛流补眉毛空隙', 'eyebrow', 'brow', 'brow',
     'Step 3. Outline and fill',
     [('先用眉刷梳理，再选接近原眉毛的颜色，在空隙处用短笔触补线。', '空隙变淡，仍看得到毛流。'),
      ('再次用螺旋眉刷梳过，使新画线条与眉毛交织。', '眉头没有一整块深色。')],
     effects=['自然', '柔和', '清晰'], suitable=['日常', 'beginner'], tools=['细头眉笔', '螺旋眉刷'],
     shortcut='只补空隙，画几根短线后梳开。',
     adaptation=['眉毛本身浓密时只整理不补色。'], minutes=2, kinds=['brow_filler'])
card('eyebrow-set-shape', '用眉胶固定眉形', 'eyebrow', 'brow', 'brow',
     'Step 4. Set them in place',
     [('擦去眉胶刷头多余产品。', '刷头没有可见胶团。'),
      ('沿已整理好的毛流轻刷一遍，让眉毛停在想要的位置。', '眉毛定向，眉间没有白色残留。')],
     effects=['毛流', '持久'], suitable=['日常', 'intermediate'], tools=['眉胶刷头'],
     shortcut='刷头先刮掉余量，再顺毛流轻刷一遍。',
     adaptation=['没有眉胶时跳过，不把其他部位的定型产品用在眉毛上。'], minutes=2,
     kinds=['brow_gel'])
card('eyebrow-soft-front', '把眉头颜色梳柔', 'eyebrow', 'brow', 'mgp_brow',
     '步骤三、步骤五',
     [('用眉粉少量填补需要补色的部分，再用干净刷头从眉头向眉中轻扫。', '眉头不再是实心色块。'),
      ('再按原毛流方向梳一遍，让眉中与眉尾颜色自然过渡。', '眉头轻、眉尾清晰。')],
     effects=['柔和', '自然', '毛流'], suitable=['日常', 'beginner'], tools=['眉粉刷', '干净螺旋眉刷'],
     shortcut='眉头太重时不加色，用干净刷往眉中带开。',
     adaptation=['只处理眉色，不按统一眉形改造用户的眉毛。'], minutes=1,
     kinds=['brow_filler'], categories=['brow', 'none'])

# Eyes: each card corresponds to a distinct application or correction.
card('eye-neutral-shadow', '浅色眼影铺上眼皮', 'eye', 'eyeshadow', 'eye',
     'How To Apply Eye Shadow / Step #1–3',
     [('用干净铺色刷少量取浅色眼影，先铺在可见上眼皮。', '睁眼能看到均匀浅色，不是一块厚粉。'),
      ('用晕染刷轻扫边缘，向眉骨方向逐渐变淡。', '眼影与裸皮之间没有硬边。')],
     effects=['浅色', '自然', '暖棕', '日常'], suitable=['日常', 'beginner'], tools=['铺色刷', '晕染刷'],
     shortcut='浅色只铺一层，边缘用干净刷扫软。',
     adaptation=['单眼皮或内双以睁眼可见范围决定铺色高度，不套用固定眼褶位置。'], minutes=3)
card('eye-primer', '眼皮薄涂妆前打底', 'eye', 'eyeshadow', 'eye_order',
     'HOW TO APPLY EYE MAKEUP / STEP #1: EYE MAKEUP PRIMER',
     [('取少量眼部打底产品，在上眼皮薄薄推匀。', '表面均匀，不见白色堆积。'),
      ('等表面稳定后再叠眼影。', '后续眼影能平滑附着。')],
     effects=['打底', '持久'], suitable=['日常', 'intermediate'], tools=['干净指腹'],
     shortcut='有眼部打底产品时薄涂一层，等稳定再上眼影。',
     adaptation=['没有眼部专用产品时可以跳过；眼周不适时停止使用。'],
     avoid=['眼周明显刺激'], minutes=1, kinds=['eye_primer'])
card('eye-crease-definition', '用中间色加深眼窝', 'eye', 'eyeshadow', 'eye',
     'How To Apply Eye Shadow / Step #4: Define your crease',
     [('中间色只取少量，从眼窝外侧向内扫。', '外侧比眼头略深。'),
      ('在颜色交界处来回轻扫，而不是把整片深色拖到眉骨。', '睁眼有层次，边界柔和。')],
     effects=['层次', '暖棕', '柔和'], suitable=['渐层', '日常'], tools=['小号晕染刷'],
     shortcut='中间色先放外侧，再用刷子余量往内晕。',
     adaptation=['内双或单眼皮让深色停在睁眼仍可见的位置，避免全眼堆深。'], minutes=3)
card('eye-blend-edges', '用干净刷晕开眼影边缘', 'eye', 'eyeshadow', 'eye',
     'How To Blend Eyeshadow Like a Pro',
     [('换干净的蓬松刷，只接触深浅两色交界。', '刷头没有额外深色。'),
      ('轻压并小幅来回扫，直到色块边界软化。', '颜色还保留明暗差，边缘没有硬线。')],
     effects=['柔和', '渐层'], suitable=['日常', '渐层'], tools=['干净晕染刷'],
     shortcut='只晕边缘，不把整片眼影刷成同一个颜色。',
     adaptation=['颜色被晕没时停止刷动，局部少量补色。'], minutes=2)
card('eye-center-highlight', '眼皮中央小范围提亮', 'eye', 'eyeshadow', 'eye',
     'How To Apply Eye Shadow / Step #6: Highlight the center',
     [('用干净小刷取少量最浅色。', '刷毛上没有大块粉。'),
      ('只轻按在上眼皮中央，边缘与周围眼影接上。', '转动视线时中央有局部亮度，范围未铺满。')],
     effects=['提亮', '香槟', 'shimmer'], suitable=['局部光泽', '渐层'], tools=['小号铺色刷'],
     shortcut='最浅色在眼皮中央点一次，再轻拍边缘。',
     adaptation=['不喜欢闪片时选哑光浅色或跳过。'], minutes=1)
card('eye-lashline', '上睫毛根部细眼线', 'eye', 'eyeliner', 'eye_order',
     'HOW TO APPLY EYE MAKEUP / STEP #4: EYELINER',
     [('从上睫毛根部画细线，先填靠近睫毛的空隙。', '睫毛根部更清晰，线条未明显变粗。'),
      ('眼尾与原睫毛线自然接住。', '正面看没有突兀的断点。')],
     effects=['清晰', '自然', '拉长'], suitable=['日常', 'beginner'], tools=['眼线笔'],
     shortcut='只画上睫毛根部外半段，逐段接起来。',
     adaptation=['手不稳时画短段再连接；不画入眼内黏膜。'], avoid=['眼周明显刺激'], minutes=2)
card('eye-short-wing', '沿眼尾带出短眼线', 'eye', 'eyeliner', 'eye_order',
     '10. ACCENT LINER / winged liner',
     [('先完成上睫毛根部外段，再从眼尾沿预期方向画一小段。', '眼尾有短延伸但不下垂成厚块。'),
      ('把延伸末端接回上眼线，留出细尖尾。', '左右眼尾方向接近。')],
     effects=['拉长', '眼尾', '清晰'], suitable=['猫眼', 'intermediate'], tools=['细头眼线笔'],
     shortcut='先只加很短的尾巴，照镜子确认方向后再延长。',
     adaptation=['下垂眼或眼褶遮住眼尾时先睁眼标出可见终点。'], minutes=3, difficulty='intermediate')
card('eye-mascara-separate', '从根部向上刷开睫毛', 'eye', 'mascara', 'mascara',
     'How To Layer Mascara / 3. Apply your first coat of mascara',
     [('把睫毛膏刷头多余膏体刮去。', '刷头没有明显膏团。'),
      ('从上睫毛根部轻微左右移动，向尖端带出一层。', '睫毛分开、根部有存在感且少结块。')],
     effects=['纤长', '清晰', '自然'], suitable=['日常', 'beginner'], tools=['睫毛膏刷头'],
     shortcut='刮掉余膏，从根部到尖端只刷一遍。',
     adaptation=['刷到眼皮时等膏体干后再轻轻清理；不与他人共用眼部刷头。'],
     avoid=['眼部感染或明显不适'], minutes=2)
card('eye-mgp-soft-gradient', '同色系浅深渐层眼影', 'eye', 'eyeshadow', 'mgp_eye',
     'Step1–Step3',
     [('浅色眼影在眼窝薄铺，分次晕染。', '上眼皮颜色均匀且轻。'),
      ('用同色系较深颜色在眼睑局部少量叠加，再扫软交界。', '局部较深，深浅自然过渡。')],
     effects=['渐层', '柔和', '同色系'], suitable=['日常', 'beginner'], tools=['晕染刷'],
     shortcut='先浅色薄铺眼窝，再用同色系深色加在局部并扫软交界。',
     adaptation=['不套用文章的双眼皮褶皱位置；单眼皮和内双以睁眼可见范围确定深色区域。'], minutes=4)

# Cheek shaping and colour are kept distinct from one another.
card('cheek-soft-blush', '苹果肌柔和腮红', 'cheek', 'blush', 'blush',
     'How to Apply Blush Symmetrically / Personalize Your Placement; Apply Your Blush',
     [('左右脸确认想要的腮红中心点，刷子取少量腮红。', '落点接近对称。'),
      ('从两侧中心轻扫向外晕开，逐层增加颜色。', '两侧有气色但无明显圆形边界。')],
     effects=['柔和', '提气色', '蜜桃', '裸粉'], suitable=['日常', 'beginner'], tools=['蓬松腮红刷'],
     shortcut='两侧先各上一薄层，照镜子比较后再补。',
     adaptation=['脸颊泛红已明显时降低上色量；不强制任何脸型定位。'], minutes=2)
card('cheek-lifted-blush', '颧骨外侧向上腮红', 'cheek', 'blush', 'blush',
     'How to Apply Blush Symmetrically / Personalize Your Placement',
     [('把腮红放在颧骨外侧较高位置，先轻扫薄层。', '腮红不集中在面中。'),
      ('向发际方向把外缘晕开，左右对照高度。', '腮红外缘渐淡，方向向上。')],
     effects=['向上', '提气色', '斜向'], suitable=['提拉', '日常'], tools=['腮红刷'],
     shortcut='腮红先放高一点，再向太阳穴方向扫软。',
     adaptation=['参考图腮红集中面中时不用此位置。'], minutes=2)
card('cheek-cream-blush', '膏状腮红双侧点拍', 'cheek', 'blush', 'blush',
     'How to Apply Blush Symmetrically / Pick Your Blush Formula and Shade; Apply Your Blush',
     [('两侧面颊相应位置各点少量膏状腮红。', '两侧起始用量接近。'),
      ('用干净指腹或海绵由中心向外轻拍，再观察是否需要增加。', '边缘融入底妆，没有膏体结块。')],
     effects=['自然', '清透', 'satin'], suitable=['日常', 'beginner'], tools=['干净指腹或海绵'],
     shortcut='左右各点少量，再分别轻拍开。',
     adaptation=['粉状定妆后若膏体拖动底妆，停止叠加并改用适合当前底妆的质地。'], minutes=2)
card('nose-soft-contour', '鼻梁两侧轻量修容', 'complexion', 'contour', 'nose_contour',
     '2. Pick Your Contour',
     [('用细刷在鼻梁两侧自然阴影处各放极少量修容色，不扩大到鼻翼中央。', '两侧范围相近，没有粗重的暗线。'),
      ('用干净刷具轻轻晕开边缘，让颜色融入周围底妆。', '正面看不到硬边，鼻部保留原有轮廓。')],
     effects=['鼻部修容', '自然', '轮廓'], suitable=['局部调整', 'intermediate'],
     tools=['细头修容刷'], shortcut='两侧各放极少量，先晕开再判断是否需要补色。',
     adaptation=['仅在参考图和用户资料支持时使用；不照搬统一鼻形，鼻翼外侧少量处理并保留原有比例。'],
     minutes=2, difficulty='intermediate')
card('cheek-outer-contour', '颧骨下方轻量修容', 'cheek', 'contour', 'contour',
     'How to Contour Your Face / Step 4. Contour your face',
     [('先确认想加深的轮廓位置，从颧骨下方外侧少量落色。', '阴影没有延伸到嘴角。'),
      ('用刷或海绵把边界晕进底妆。', '正面没有硬边，侧面轮廓有轻微层次。')],
     effects=['修容', '轮廓', '自然', 'matte'], suitable=['日常', 'intermediate'], tools=['小号修容刷或海绵'],
     shortcut='先从靠耳一侧放少量颜色，向面中渐淡。',
     adaptation=['不依据照片自动判定脸型；用户确认想加强的区域后再扩大范围。'], minutes=3, difficulty='intermediate')
card('cheek-cheekbone-highlight', '颧骨高点少量高光', 'cheek', 'highlighter', 'highlight',
     'Where to Apply Highlighter to Your Face / 1. Cheekbones; Step 4: Apply your highlighter',
     [('取少量高光，定位颧骨上方受光处。', '高光不落在整片脸颊。'),
      ('轻扫或轻拍成薄层，再转头检查反光边界。', '转头时出现局部光泽，正面没有明显粉线。')],
     effects=['高光', '提亮', '光泽', 'shimmer'], suitable=['日常', '局部光泽'], tools=['小号高光刷或干净指腹'],
     shortcut='两侧颧骨高点各点少量，先看自然光效果。',
     adaptation=['全哑光参考妆可跳过；不喜欢闪片时选更细腻的质地。'], minutes=2)
card('cheek-mgp-diffused-blush', '外侧至面中渐淡腮红', 'cheek', 'blush', 'mgp_blush',
     '万能通勤腮红',
     [('在面颊外侧少量上腮红，确定斜向面中的路径。', '外侧是最清楚的颜色落点。'),
      ('用刷具余粉向面中轻扫，让颜色逐渐减淡。', '外深内浅，没有两端一样重的色带。')],
     effects=['渐淡', '修饰', '斜向'], suitable=['柔和轮廓', 'intermediate'], tools=['腮红刷'],
     shortcut='从外侧开始，越向面中越轻，不在面中重新取粉。',
     adaptation=['文章按脸型举例；本卡只提供可选画法，不将用户归类为某种脸型。'], minutes=3)
# Lips: preparation, edge, fill and finish are separate decisions.
card('lip-balm-prep', '润唇后再上色', 'lip', 'lip', 'lip',
     'How To Prep Your Lips for Lipstick',
     [('先确认唇部无明显破损，再薄涂适合自己的润唇产品。', '唇面不紧绷，也不留下厚油层。'),
      ('待表面适合上色后再用口红。', '口红能平滑接触唇面。')],
     effects=['滋润', '平滑'], suitable=['日常', 'beginner'], tools=['润唇产品'],
     shortcut='先薄涂润唇，再上口红；油层过厚时轻按掉余量。',
     adaptation=['唇部明显破损或刺激时暂停唇妆；不做医疗判断。'], avoid=['唇部破损或明显不适'], minutes=1,
     kinds=['lip_balm'])
card('lip-natural-line', '沿原唇缘描出轮廓', 'lip', 'lip', 'lip_liner',
     'How to Apply Lip Liner / 1. Determine your lip shape; 4. Line your lips',
     [('先观察唇峰和唇角，选与计划唇色相近的唇线笔。', '能沿原唇缘找到起点。'),
      ('从唇峰向两侧画短线，再连接下唇自然边界。', '唇缘连续但不过分外扩。')],
     effects=['清晰', '唇形'], suitable=['精致', 'intermediate'], tools=['唇线笔'],
     shortcut='从唇峰开始沿原唇缘画短线，画错时用棉签修正。',
     adaptation=['不想强调唇缘时跳过唇线；不能从参考图推断真实唇形调整。'], minutes=2,
     kinds=['lip_liner'])
card('lip-even-lipstick', '均匀填满唇色', 'lip', 'lip', 'lip',
     'How To Put On Lipstick',
     [('沿上唇与下唇的轮廓轻涂第一层。', '唇角和唇中都覆盖到。'),
      ('用唇刷或口红补齐未上色处，棉签清理越界。', '唇色均匀，唇缘干净。')],
     effects=['均匀', '柔雾', '豆沙'], suitable=['日常', 'beginner'], tools=['口红或唇刷', '棉签'],
     shortcut='先上一薄层，照镜子找空缺后再补，不一次厚涂。',
     adaptation=['想要清透效果时只保留第一层；颜色由用户现有产品和参考图决定。'], minutes=2,
     kinds=['lip_color'])
card('lip-center-gloss', '唇中央叠少量唇蜜', 'lip', 'lip', 'lip_gloss',
     'HOW TO APPLY LIP GLOSS; 3 LIP GLOSS HACKS / 1. Temporarily “plump” your lips',
     [('在已有唇色上从唇中央点少量唇蜜。', '中央能看到一小块光泽。'),
      ('用刷头余量向外轻带，不把唇缘涂得过厚。', '中央亮、边缘整洁。')],
     effects=['光泽', '水光', 'glossy'], suitable=['清透', '局部光泽'], tools=['唇蜜刷头'],
     shortcut='只在上下唇中央各点一次，不需要涂满。',
     adaptation=['参考妆为全哑光或用户不喜欢黏腻质地时跳过。'], minutes=1,
     kinds=['lip_gloss'])
card('lip-mgp-inner-gradient', '唇内侧渐变色', 'lip', 'lip', 'mgp_lip',
     '日常又减龄的渐变唇',
     [('用少量唇色薄涂完整上下唇，先建立均匀底色。', '整个唇面有淡淡颜色。'),
      ('只在上下唇内侧加一层颜色，再轻拍深浅交界。', '内侧较深，向唇缘自然变浅。')],
     effects=['渐变', '柔和', '提气色'], suitable=['日常', 'beginner'], tools=['口红或唇刷'],
     shortcut='先薄涂全唇，再只加深内侧，最后拍柔交界。',
     adaptation=['色号按用户已有唇部产品选择；参考图是均匀饱满唇时不用渐变。'], minutes=2,
     kinds=['lip_color'])


def main() -> None:
    assert len(cards) == len({item['id'] for item in cards})
    OUT.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {len(cards)} sourced technique cards to {OUT}')


if __name__ == '__main__':
    main()
