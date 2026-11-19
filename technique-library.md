# 技法卡来源与重建

技法库收录 29 张依据官方公开教程编写的结构化技法卡，覆盖底妆与鼻部修容 6、眉妆 3、眼妆 9、面颊（腮红、修容、高光）6、唇妆 5。鼻部修容卡对应[欧莱雅巴黎鼻部修容教程的第 2 步](https://www.lorealparisusa.com/beauty-magazine/makeup/face-makeup/nose-contour)。每张卡都记录来源机构、文章标题、原文 URL、对应章节和查阅日期，包含具体动作、完成信号、简化做法、适用调整、工具与产品类别。卡片是对公开教程的重新编写，不是原文或配图的复制。

主来源是[欧莱雅巴黎官网 Beauty Magazine](https://www.lorealparisusa.com/beauty-magazine/makeup)，用于底妆、眉眼、腮红、修容、高光及唇妆的通用操作。中文补充来源是毛戈平形象设计艺术学校的[眉妆](https://www.maogp.com/news_nr/index/730/39300.html)、[眼妆](https://www.maogp.com/news_nr/index/547/41283.html)和[腮红与渐变唇](https://www.maogp.com/news_nr/index/730/41888.html)公开文章。后者是学校编辑发布的彩妆资讯，不能表述为毛戈平本人对本项目授课、审阅或背书。品牌和学校文章均不是经过独立同行评审的技术标准；部分内容含商品推广或特定审美偏好，因此卡片只保留可操作的上妆动作，并允许用户根据参考图和自身偏好调整。

眉头梳柔卡允许“只用干净眉刷整理毛流”的无需产品类别；若教程选择眉妆产品类别，仍需明确实际补色或定型操作。卡库由 `scripts/build_sourced_techniques.py` 生成，类别变更必须同步写回生成脚本。

数据源在 `scripts/build_sourced_techniques.py`，运行 `python scripts/build_sourced_techniques.py` 会重建 `data/knowledge_cards.json`。运行时从该 JSON 文件加载技法卡。教程保存生成时的步骤与示意图快照，可在技法库更新后继续查看原版本。

教程步骤保存所用技法卡的原文链接和章节，网页可直接打开原文。技法卡以独立字段记录来源和审阅状态。

粉底、遮瑕、定妆粉、眉胶、眼部打底、润唇膏和唇线笔等卡片另有具体产品用途约束。匹配先按商品目录里的部位类别筛选，再根据产品名称确认具体用途。用途不明确时，界面提供核实或补录提示。
