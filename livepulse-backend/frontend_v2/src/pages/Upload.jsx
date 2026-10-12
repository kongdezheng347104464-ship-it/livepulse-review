import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Upload as AntUpload,
  Button,
  Form,
  Input,
  Select,
  message,
  Progress,
  Radio,
} from "antd";
import UploadOutlined from "@ant-design/icons/UploadOutlined";
import client from "../api/client";
import { Icon } from "../components/layout/IconSprite";

const { Dragger: AntDragger } = AntUpload;
const MAX_BYTES = 2 * 1024 * 1024 * 1024; // 2 GB

/**
 * Upload — 跟旧 frontend 行为一致（FormData / anchor_id XOR new_anchor_name /
 * 409 自动切换 / 进度条 / 大文件 timeout=0）。
 *
 * UI 用 v2 风格的 page header + card，但 Dragger / Form / Progress
 * 这些复杂交互保留 AntD 实现（按 owner 决定的"部分保留 AntD"策略）。
 */
export default function Upload() {
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const presetAnchorId = searchParams.get("anchor_id");

  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [anchors, setAnchors] = useState([]);
  const [anchorMode, setAnchorMode] = useState(
    presetAnchorId ? "existing" : "existing"
  );

  useEffect(() => {
    let cancelled = false;
    client
      .get("/anchors/", { params: { page_size: 100 } })
      .then((res) => {
        if (cancelled) return;
        const items = res?.items || [];
        setAnchors(items);
        if (presetAnchorId && items.some((a) => a.id === presetAnchorId)) {
          form.setFieldsValue({ anchor_id: presetAnchorId });
        } else if (items.length > 0) {
          form.setFieldsValue({ anchor_id: items[0].id });
        } else {
          setAnchorMode("new");
        }
      })
      .catch((err) => {
        console.error("Failed to load anchors:", err);
        if (!cancelled) setAnchorMode("new");
      });
    return () => {
      cancelled = true;
    };
  }, [form, presetAnchorId]);

  const handleUpload = async (values) => {
    const fileList = form.getFieldValue("video");
    if (!fileList || fileList.length === 0) {
      message.warning("请选择视频文件");
      return;
    }
    const file = fileList[0].originFileObj || fileList[0];

    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", values.title || file.name);

    if (anchorMode === "existing") {
      if (!values.anchor_id) {
        message.warning("请选择主播");
        return;
      }
      fd.append("anchor_id", values.anchor_id);
    } else {
      if (!values.new_anchor_name?.trim()) {
        message.warning("请输入新主播名称");
        return;
      }
      fd.append("new_anchor_name", values.new_anchor_name.trim());
    }

    setUploading(true);
    setProgress(0);
    try {
      const res = await client.post("/upload/video", fd, {
        headers: { "Content-Type": "multipart/form-data" },
        // 大文件 (2GB) 上传时长可能 30 分钟，禁用默认 30s timeout
        timeout: 0,
        onUploadProgress: (e) => {
          if (e.total) {
            // 上传到 95%，留 5% 给"backend 接收 + 创建 session"过渡
            // 旧版到 80% 留太多，用户看进度条卡很久很慌
            setProgress(Math.round((e.loaded / e.total) * 95));
          }
        },
      });
      setProgress(100);
      message.success("上传成功，分析任务已启动");
      // res.session_id (v2 已自动 unwrap envelope)
      setTimeout(
        () => navigate(`/sessions/${res.session_id}/report`),
        800
      );
    } catch (err) {
      // 409 anchor 重名 → auto-switch existing + 预选
      const detail = err.detail;
      if (
        err.status === 409 &&
        typeof detail === "object" &&
        detail?.existing_id
      ) {
        setAnchorMode("existing");
        form.setFieldsValue({
          anchor_id: detail.existing_id,
          new_anchor_name: undefined,
        });
        message.warning(
          `该名称与已有主播「${detail.existing_name}」重复，已切换为"选择已有主播"并选中该主播`,
          6
        );
      } else {
        message.error(err.message || "上传失败");
      }
    } finally {
      setUploading(false);
    }
  };

  const normFile = (e) => (Array.isArray(e) ? e : e?.fileList || []);

  return (
    <section className="upload-view">
      <div className="view-header">
        <div>
          <h2 className="anchor-title">上传直播录屏</h2>
          <div className="anchor-meta">
            支持 MP4 / MKV / MOV / WebM / AVI · 单文件 ≤ 2 GB
          </div>
        </div>
      </div>

      <div className="upload-card">
        <Form
          form={form}
          layout="vertical"
          onFinish={handleUpload}
          requiredMark={false}
        >
          <Form.Item
            name="video"
            label="视频文件"
            rules={[{ required: true, message: "请选择视频文件" }]}
            valuePropName="fileList"
            getValueFromEvent={normFile}
          >
            <AntDragger
              accept=".mp4,.mkv,.mov,.webm,.avi"
              maxCount={1}
              beforeUpload={(file) => {
                if (file.size > MAX_BYTES) {
                  message.error(
                    `文件 ${(file.size / 1024 / 1024 / 1024).toFixed(
                      1
                    )} GB 超过 2 GB 上限`
                  );
                  return AntUpload.LIST_IGNORE;
                }
                // 自动填标题（无 ext）
                if (!form.getFieldValue("title")) {
                  form.setFieldsValue({
                    title: file.name.replace(/\.[^/.]+$/, ""),
                  });
                }
                return false;
              }}
              style={{ padding: "20px 0" }}
            >
              <p style={{ margin: 0, fontSize: 26, color: "var(--accent)" }}>
                <UploadOutlined />
              </p>
              <p
                style={{
                  margin: "8px 0 4px",
                  fontSize: 14,
                  color: "var(--text)",
                }}
              >
                拖拽视频到此处，或点击选择
              </p>
              <p style={{ margin: 0, fontSize: 11, color: "var(--text-mute)" }}>
                单文件 ≤ 2 GB
              </p>
            </AntDragger>
          </Form.Item>

          <Form.Item label="主播">
            <Radio.Group
              value={anchorMode}
              onChange={(e) => setAnchorMode(e.target.value)}
              style={{ marginBottom: 8 }}
            >
              <Radio.Button
                value="existing"
                disabled={anchors.length === 0}
              >
                选择已有主播 ({anchors.length})
              </Radio.Button>
              <Radio.Button value="new">+ 新建主播</Radio.Button>
            </Radio.Group>
            {anchorMode === "existing" && (
              <Form.Item name="anchor_id" noStyle>
                <Select
                  className="upload-anchor-select"
                  placeholder="请选择主播"
                  options={anchors.map((a) => ({
                    label: `${a.name}（${a.session_count} 场）`,
                    value: a.id,
                  }))}
                  showSearch
                  optionFilterProp="label"
                  style={{ width: "100%" }}
                />
              </Form.Item>
            )}
            {anchorMode === "new" && (
              <Form.Item
                name="new_anchor_name"
                noStyle
                rules={[
                  { required: true, message: "请输入主播名称" },
                  { max: 100, message: "名称最长 100 字符" },
                ]}
              >
                <Input placeholder="例如：小满" maxLength={100} />
              </Form.Item>
            )}
            <div
              style={{
                fontSize: 11,
                color: "var(--text-mute)",
                marginTop: 6,
              }}
            >
              {anchorMode === "existing"
                ? "建议复用已有主播，便于历史对比与趋势分析"
                : "系统会检查名称是否与已有主播重复（自动忽略空格、大小写、全半角差异）"}
            </div>
          </Form.Item>

          <Form.Item
            name="title"
            label="直播标题"
            extra={
              <span style={{ fontSize: 11, color: "var(--text-mute)" }}>
                选完视频后会自动填入文件名，可手动修改
              </span>
            }
          >
            <Input placeholder="选视频后自动填" />
          </Form.Item>

          {uploading && (
            <div style={{ marginBottom: 16 }}>
              <Progress percent={progress} status="active" />
              <div
                style={{
                  marginTop: 6,
                  fontSize: 12,
                  color: "var(--text-mute)",
                  textAlign: "center",
                }}
              >
                {progress < 95
                  ? "上传中…"
                  : progress < 100
                  ? "上传完成，正在创建分析任务…"
                  : "任务已启动，跳转到分析报告页…"}
              </div>
            </div>
          )}

          <Form.Item style={{ marginBottom: 0 }}>
            <div style={{ display: "flex", gap: 10 }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={uploading}
                style={{
                  background: "var(--text)",
                  borderColor: "var(--text)",
                  height: 36,
                  padding: "0 20px",
                  fontWeight: 500,
                }}
              >
                <Icon name="plus" size={14} />
                <span style={{ marginLeft: 4 }}>上传并分析</span>
              </Button>
              <Button onClick={() => navigate(-1)} style={{ height: 36 }}>
                取消
              </Button>
            </div>
          </Form.Item>
        </Form>
      </div>
    </section>
  );
}
