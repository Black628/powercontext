# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bundled study notes.

They are plain text on purpose: the demo must stay runnable offline, and the
same corpus makes the verification sample reproducible. Each note is short
enough to become one passage, and the three courses overlap in vocabulary so
that a query like 「正则化 过拟合」 is a real ranking problem rather than a
single-row lookup.
"""

from __future__ import annotations

from typing import Any

SAMPLES: list[dict[str, Any]] = [
    {
        "course": "机器学习",
        "title": "过拟合与欠拟合",
        "text": (
            "过拟合指模型在训练集上误差很小、在验证集上误差明显变大，本质是模型把训练数据里的噪声也学成了规律。"
            "常见信号是训练损失持续下降而验证损失回升，两者之间的差距越大，过拟合越严重。"
            "欠拟合相反：训练集和验证集的误差都高，模型容量不足或特征太少。"
            "判断顺序应该是先看训练误差，再看训练与验证的差距，最后才谈调参。"
        ),
    },
    {
        "course": "机器学习",
        "title": "正则化",
        "text": (
            "正则化通过给目标函数加上参数惩罚项来限制模型复杂度，从而缓解过拟合。"
            "L2 正则（权重衰减）把参数压小，使模型更平滑；L1 正则会把部分权重压到 0，得到稀疏解，因此也能做特征选择。"
            "正则化系数 λ 控制惩罚强度：λ 太大导致欠拟合，λ 太小等于没有正则化。"
            "实践中常用交叉验证选择 λ，而不是在训练集上直接最小化损失。"
        ),
    },
    {
        "course": "机器学习",
        "title": "交叉验证",
        "text": (
            "K 折交叉验证把训练数据分成 K 份，轮流用其中一份做验证、其余做训练，最后对 K 次结果取平均。"
            "它的作用是让模型评估不受单次划分的偶然性影响，尤其适合样本量小的场景。"
            "K 的常见取值是 5 或 10：太小偏差大，太大计算成本高。"
            "如果数据有时间顺序，必须用时间序列切分，否则会泄漏未来信息，交叉验证的结果会虚高。"
        ),
    },
    {
        "course": "概率论",
        "title": "条件概率与贝叶斯公式",
        "text": (
            "条件概率 P(A|B) 表示在 B 发生的条件下 A 发生的概率，定义是 P(A∩B)/P(B)，要求 P(B)>0。"
            "贝叶斯公式把它反转：P(A|B) = P(B|A)P(A)/P(B)，其中 P(A) 是先验，P(B|A) 是似然，P(A|B) 是后验。"
            "贝叶斯公式的价值在于：直接估计后验往往很难，但先验和似然更容易从数据里统计出来。"
            "当分母 P(B) 难以计算时，常利用全概率公式把 P(B) 拆成若干互斥情形的加权和。"
        ),
    },
    {
        "course": "概率论",
        "title": "期望的线性性",
        "text": (
            "期望的线性性指 E(aX+bY) = aE(X)+bE(Y)，无论 X 与 Y 是否独立都成立。"
            "它把复杂随机变量的期望拆成若干简单部分之和，常用于计数类问题：把指示变量相加再求期望。"
            "注意方差没有类似性质：Var(X+Y) = Var(X)+Var(Y)+2Cov(X,Y)，只有在 X 与 Y 不相关时协方差才为 0。"
        ),
    },
    {
        "course": "概率论",
        "title": "大数定律与中心极限定理",
        "text": (
            "大数定律说明样本均值依概率收敛到总体均值，它解释的是「样本多了估计就准」；"
            "中心极限定理说明样本均值经标准化后依分布收敛到标准正态分布，它解释的是「误差大约服从正态分布」。"
            "两者结论不同：前者谈收敛到常数，后者谈波动形态。"
            "中心极限定理要求样本独立同分布且方差有限，样本量通常 30 以上近似效果就可以接受。"
        ),
    },
    {
        "course": "操作系统",
        "title": "进程与线程",
        "text": (
            "进程是资源分配的基本单位，拥有独立的地址空间；线程是 CPU 调度的基本单位，同一进程内的线程共享地址空间与文件描述符。"
            "因此线程切换的开销比进程切换小，但线程之间共享数据也带来了竞态条件，需要加锁保护临界区。"
            "进程之间通信要用管道、共享内存或消息队列；线程之间直接读写共享变量即可，但要配合同步原语。"
            "崩溃隔离也不同：一个线程崩溃可能带走整个进程，进程之间则互相独立。"
        ),
    },
    {
        "course": "操作系统",
        "title": "死锁的四个必要条件",
        "text": (
            "死锁需要同时满足四个条件：互斥、占有并等待、不可剥夺、循环等待。"
            "破坏其中任意一个条件就能预防死锁：例如一次性申请全部资源破坏占有并等待，按资源编号顺序申请破坏循环等待。"
            "避免死锁的经典算法是银行家算法，它在分配前检查系统是否仍处于安全状态。"
            "死锁检测与恢复则允许死锁发生，通过资源分配图找环，再回滚或终止进程。"
        ),
    },
    {
        "course": "操作系统",
        "title": "虚拟内存与页面置换",
        "text": (
            "虚拟内存用页表把进程的虚拟地址映射到物理页框，使每个进程都以为自己拥有连续的地址空间。"
            "缺页中断发生时操作系统把一个页面从磁盘调入内存；如果内存已满，就要按页面置换算法淘汰一个页面。"
            "FIFO 简单但会出现 Belady 异常，即分配更多页框反而缺页更多；LRU 近似最优，但完整实现代价高，实际多用时钟算法近似。"
            "页面置换算法的评价指标是缺页率，测试时用固定引用串比较各算法的缺页次数。"
        ),
    },
]
