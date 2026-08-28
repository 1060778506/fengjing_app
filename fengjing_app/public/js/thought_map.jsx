import React, { useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

const 初始小球列表 = [
    { id: "ball_1", name: "小球 A", type: "订单销售", amount: 120.5, x: 260, y: 240, color: "#2563eb" },
    { id: "ball_2", name: "小球 B", type: "订单销售", amount: 88.2, x: 460, y: 320, color: "#16a34a" }
];

function SimpleModApp() {
    const [小球列表, 设置小球列表] = useState(初始小球列表);
    const [连线列表, 设置连线列表] = useState([]);
    const [多选模式, 设置多选模式] = useState(false);
    const [连线模式, 设置连线模式] = useState(false);
    const [选中ID列表, 设置选中ID列表] = useState([]);
    const [连线起点ID, 设置连线起点ID] = useState(null);
    const [视图偏移, 设置视图偏移] = useState({ x: 0, y: 0 });
    const [选框, 设置选框] = useState(null);
    const 拖拽Ref = useRef(null);
    const 忽略下一次点击Ref = useRef(false);

    const 当前选中小球列表 = useMemo(
        () => 小球列表.filter(小球 => 选中ID列表.includes(小球.id)),
        [小球列表, 选中ID列表]
    );

    const 当前详情 = 当前选中小球列表[当前选中小球列表.length - 1] || null;

    function 点击空白() {
        if (忽略下一次点击Ref.current) {
            忽略下一次点击Ref.current = false;
            return;
        }
        设置选中ID列表([]);
        设置连线起点ID(null);
    }

    function 按下画布(事件) {
        if (多选模式) {
            拖拽Ref.current = {
                type: "selectBox",
                startX: 事件.clientX,
                startY: 事件.clientY,
                moved: false
            };
            设置选框({ x: 事件.clientX, y: 事件.clientY, width: 0, height: 0 });
            return;
        }

        拖拽Ref.current = {
            type: "pan",
            startX: 事件.clientX,
            startY: 事件.clientY,
            offsetX: 视图偏移.x,
            offsetY: 视图偏移.y,
            moved: false
        };
    }

    function 按下小球(事件, 小球) {
        eventStop(事件);
        事件.currentTarget.setPointerCapture?.(事件.pointerId);
        拖拽Ref.current = {
            type: "ball",
            id: 小球.id,
            startX: 事件.clientX,
            startY: 事件.clientY,
            ballX: 小球.x,
            ballY: 小球.y,
            moved: false
        };
    }

    function 指针移动(事件) {
        const 拖拽 = 拖拽Ref.current;
        if (!拖拽) return;

        const 移动X = 事件.clientX - 拖拽.startX;
        const 移动Y = 事件.clientY - 拖拽.startY;
        if (Math.abs(移动X) + Math.abs(移动Y) > 3) 拖拽.moved = true;

        if (拖拽.type === "pan") {
            设置视图偏移({
                x: 拖拽.offsetX + 移动X,
                y: 拖拽.offsetY + 移动Y
            });
            return;
        }

        if (拖拽.type === "selectBox") {
            设置选框({
                x: Math.min(拖拽.startX, 事件.clientX),
                y: Math.min(拖拽.startY, 事件.clientY),
                width: Math.abs(移动X),
                height: Math.abs(移动Y)
            });
            return;
        }

        if (拖拽.type === "ball") {
            设置小球列表(旧列表 => 旧列表.map(小球 =>
                小球.id === 拖拽.id
                    ? { ...小球, x: 拖拽.ballX + 移动X, y: 拖拽.ballY + 移动Y }
                    : 小球
            ));
        }
    }

    function 指针松开() {
        if (拖拽Ref.current?.type === "selectBox") {
            const 当前选框 = 选框;
            if (当前选框 && 当前选框.width > 4 && 当前选框.height > 4) {
                const 命中ID列表 = 小球列表
                    .filter(小球 => {
                        const 屏幕X = 小球.x + 视图偏移.x;
                        const 屏幕Y = 小球.y + 视图偏移.y;
                        return (
                            屏幕X >= 当前选框.x &&
                            屏幕X <= 当前选框.x + 当前选框.width &&
                            屏幕Y >= 当前选框.y &&
                            屏幕Y <= 当前选框.y + 当前选框.height
                        );
                    })
                    .map(小球 => 小球.id);
                设置选中ID列表(命中ID列表);
            }
            设置选框(null);
        }

        if (拖拽Ref.current?.moved) {
            忽略下一次点击Ref.current = true;
        }
        拖拽Ref.current = null;
    }

    function 点击小球(事件, 小球) {
        eventStop(事件);
        if (忽略下一次点击Ref.current) {
            忽略下一次点击Ref.current = false;
            return;
        }

        if (连线模式) {
            if (!连线起点ID) {
                设置连线起点ID(小球.id);
                设置选中ID列表([小球.id]);
                return;
            }

            if (连线起点ID !== 小球.id) {
                const 已存在 = 连线列表.some(线 =>
                    (线.from === 连线起点ID && 线.to === 小球.id) ||
                    (线.from === 小球.id && 线.to === 连线起点ID)
                );
                if (!已存在) {
                    设置连线列表([...连线列表, { id: `line_${Date.now()}`, from: 连线起点ID, to: 小球.id }]);
                }
            }

            设置连线起点ID(null);
            设置选中ID列表([小球.id]);
            return;
        }

        if (多选模式) {
            设置选中ID列表(旧列表 =>
                旧列表.includes(小球.id)
                    ? 旧列表.filter(id => id !== 小球.id)
                    : [...旧列表, 小球.id]
            );
            return;
        }

        设置选中ID列表([小球.id]);
    }

    function 新建小球() {
        const 序号 = 小球列表.length + 1;
        const 新小球 = {
            id: `ball_${Date.now()}`,
            name: `小球 ${序号}`,
            type: "手动新增",
            amount: 0,
            x: 220 + 序号 * 70,
            y: 180 + 序号 * 42,
            color: "#0f766e"
        };
        设置小球列表([...小球列表, 新小球]);
        设置选中ID列表([新小球.id]);
    }

    function 删除选中() {
        if (选中ID列表.length === 0) return;
        设置小球列表(旧列表 => 旧列表.filter(小球 => !选中ID列表.includes(小球.id)));
        设置连线列表(旧列表 => 旧列表.filter(线 => !选中ID列表.includes(线.from) && !选中ID列表.includes(线.to)));
        设置选中ID列表([]);
        设置连线起点ID(null);
    }

    function 切换多选() {
        设置多选模式(当前值 => !当前值);
        设置连线模式(false);
        设置连线起点ID(null);
        设置选框(null);
        设置选中ID列表([]);
    }

    function 切换连线() {
        设置连线模式(当前值 => !当前值);
        设置多选模式(false);
        设置连线起点ID(null);
        设置选中ID列表([]);
    }

    function 小球ById(id) {
        return 小球列表.find(小球 => 小球.id === id);
    }

    return (
        <div style={styles.app}>
            <div style={styles.toolbar}>
                <button style={toolStyle(多选模式)} onClick={切换多选}>多选</button>
                <button style={styles.toolButton} onClick={新建小球}>新建</button>
                <button style={styles.toolButton} onClick={删除选中}>删除</button>
                <button style={toolStyle(连线模式)} onClick={切换连线}>连线</button>
            </div>

            <main
                style={styles.stage}
                onClick={点击空白}
                onPointerDown={按下画布}
                onPointerMove={指针移动}
                onPointerUp={指针松开}
                onPointerLeave={指针松开}
            >
                <svg style={styles.svg}>
                    {连线列表.map(线 => {
                        const 起点 = 小球ById(线.from);
                        const 终点 = 小球ById(线.to);
                        if (!起点 || !终点) return null;
                        return (
                            <line
                                key={线.id}
                                x1={起点.x + 视图偏移.x}
                                y1={起点.y + 视图偏移.y}
                                x2={终点.x + 视图偏移.x}
                                y2={终点.y + 视图偏移.y}
                                stroke="#94a3b8"
                                strokeWidth="2"
                            />
                        );
                    })}
                </svg>

                {小球列表.map(小球 => {
                    const 已选中 = 选中ID列表.includes(小球.id);
                    const 是连线起点 = 连线起点ID === 小球.id;
                    return (
                        <button
                            key={小球.id}
                            type="button"
                            style={{
                                ...styles.ball,
                                left: 小球.x + 视图偏移.x,
                                top: 小球.y + 视图偏移.y,
                                background: 小球.color,
                                outline: 已选中 ? "4px solid #fbbf24" : "none",
                                boxShadow: 是连线起点
                                    ? "0 0 0 8px rgba(59, 130, 246, 0.25)"
                                    : "0 10px 24px rgba(15, 23, 42, 0.18)"
                            }}
                            onPointerDown={事件 => 按下小球(事件, 小球)}
                            onClick={事件 => 点击小球(事件, 小球)}
                        >
                            {小球.name}
                        </button>
                    );
                })}

                {选框 && (
                    <div
                        style={{
                            ...styles.selectBox,
                            left: 选框.x,
                            top: 选框.y,
                            width: 选框.width,
                            height: 选框.height
                        }}
                    />
                )}
            </main>

            <aside style={styles.detail}>
                {当前详情 ? (
                    <>
                        <h2 style={styles.title}>{当前详情.name}</h2>
                        <Field name="ID" value={当前详情.id} />
                        <Field name="类型" value={当前详情.type} />
                        <Field name="金额" value={当前详情.amount.toFixed(2)} />
                        <Field name="坐标" value={`x: ${当前详情.x}, y: ${当前详情.y}`} />
                        {多选模式 && <Field name="多选数量" value={当前选中小球列表.length} />}
                    </>
                ) : (
                    <div style={styles.empty}>点击小球查看详细信息</div>
                )}
            </aside>
        </div>
    );
}

function Field({ name, value }) {
    return (
        <div style={styles.field}>
            <span style={styles.fieldName}>{name}</span>
            <span style={styles.fieldValue}>{value}</span>
        </div>
    );
}

function eventStop(事件) {
    事件.preventDefault();
    事件.stopPropagation();
}

function toolStyle(active) {
    return {
        ...styles.toolButton,
        background: active ? "#2563eb" : "#ffffff",
        color: active ? "#ffffff" : "#334155",
        borderColor: active ? "#2563eb" : "#cbd5e1"
    };
}

const styles = {
    app: {
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 620,
        overflow: "hidden",
        background: "#f8fafc",
        color: "#0f172a",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif'
    },
    toolbar: {
        position: "absolute",
        top: 18,
        right: 390,
        zIndex: 20,
        display: "flex",
        gap: 8,
        padding: 8,
        background: "rgba(255, 255, 255, 0.92)",
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        boxShadow: "0 12px 28px rgba(15, 23, 42, 0.12)"
    },
    toolButton: {
        height: 34,
        padding: "0 14px",
        border: "1px solid #cbd5e1",
        borderRadius: 6,
        background: "#ffffff",
        color: "#334155",
        fontWeight: 700,
        cursor: "pointer"
    },
    stage: {
        position: "absolute",
        inset: "0 360px 0 0"
    },
    svg: {
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none"
    },
    ball: {
        position: "absolute",
        width: 74,
        height: 74,
        marginLeft: -37,
        marginTop: -37,
        border: "2px solid rgba(255,255,255,0.85)",
        borderRadius: "50%",
        color: "#ffffff",
        fontSize: 13,
        fontWeight: 800,
        cursor: "pointer"
    },
    selectBox: {
        position: "fixed",
        zIndex: 30,
        border: "1px solid #2563eb",
        background: "rgba(37, 99, 235, 0.12)",
        pointerEvents: "none"
    },
    detail: {
        position: "absolute",
        top: 0,
        right: 0,
        width: 360,
        height: "100%",
        padding: 24,
        background: "#ffffff",
        borderLeft: "1px solid #e2e8f0",
        boxSizing: "border-box"
    },
    title: {
        margin: "0 0 18px",
        fontSize: 22,
        fontWeight: 800
    },
    field: {
        padding: "12px 0",
        borderBottom: "1px solid #f1f5f9"
    },
    fieldName: {
        display: "block",
        color: "#94a3b8",
        fontSize: 12,
        fontWeight: 700
    },
    fieldValue: {
        display: "block",
        marginTop: 4,
        color: "#334155",
        fontSize: 14,
        fontWeight: 600
    },
    empty: {
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#94a3b8",
        fontSize: 14
    }
};

const 容器 = document.getElementById("app-root");
const 根节点 = createRoot(容器);
根节点.render(<SimpleModApp />);
