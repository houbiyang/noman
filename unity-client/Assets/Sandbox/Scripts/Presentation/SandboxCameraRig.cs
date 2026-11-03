using System;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Qiongtu.Sandbox
{
    /// <summary>总览、旋转、缩放和单位跟随相机；任何输入都不修改仿真状态。</summary>
    [DisallowMultipleComponent]
    public sealed class SandboxCameraRig : MonoBehaviour
    {
        private Camera viewCamera;
        private SandboxWorld world;
        private Vector3 focus = new Vector3(60, 0, 39);
        private Vector3 overviewFocus;
        private float distance = 106;
        private float overviewDistance;
        private float yaw;
        private float overviewYaw;
        private float pitch = 53;
        private float overviewPitch;
        private string followedId;
        private Vector2 mouseDownPosition;
        private Vector2 previousPointerPosition;
        private bool pointerDragged;
        private PointerGesture pointerGesture;

        /// <summary>一次按下到松开只处理一种地图手势，界面区域不接管手势。</summary>
        private enum PointerGesture
        {
            None,
            SelectOrPan,
            Pan,
            Rotate
        }

        /// <summary>判定指针是否位于可交互界面上。</summary>
        public Func<Vector2, bool> IsPointerOverUi { get; set; }

        /// <summary>判定是否正在编辑表单，避免相机抢占键盘。</summary>
        public Func<bool> IsEditingUi { get; set; }

        /// <summary>用户点选单位后通知页面。</summary>
        public event Action<string> UnitSelected;
        public Func<bool> IsMapCommand;
        public event Action<Vector3> MapClicked;
        public bool TryMapPoint(Vector2 screen, out Vector3 point)
        {
            Ray ray = viewCamera.ScreenPointToRay(screen);
            float height = 0; point = Vector3.zero;
            for (int i = 0; i < 8; i++)
            {
                var plane = new Plane(Vector3.up, new Vector3(0, height, 0));
                if (!plane.Raycast(ray, out float distance)) return false;
                point = ray.GetPoint(distance); height = SandboxWorld.Surface(point.x, point.z).y;
            }
            return point.x >= 0 && point.x <= world.MapSize.x && point.z >= 0 && point.z <= world.MapSize.y;
        }

        /// <summary>跟随状态改变后通知页面。</summary>
        public event Action ViewChanged;

        /// <summary>是否正在跟随当前可见单位。</summary>
        public bool IsFollowing => followedId != null;

        /// <summary>创建透视相机并绑定当前显示场景。</summary>
        /// <param name="sandboxWorld">可见单位查询来源。</param>
        public void Initialize(SandboxWorld sandboxWorld)
        {
            world = sandboxWorld;
            viewCamera = gameObject.AddComponent<Camera>();
            gameObject.tag = "MainCamera";
            viewCamera.fieldOfView = 46;
            viewCamera.nearClipPlane = 0.15f;
            viewCamera.farClipPlane = 1600;
            viewCamera.clearFlags = CameraClearFlags.SolidColor;
            viewCamera.backgroundColor = new Color(0.58f, 0.61f, 0.61f);
            viewCamera.allowHDR = true;
            ResetOverview();
        }

        /// <summary>对可见单位进行屏幕射线点选。</summary>
        /// <param name="screenPosition">以屏幕左下角为原点的位置。</param>
        /// <returns>当前允许显示的单位标识；未选中返回空。</returns>
        public string PickUnit(Vector2 screenPosition)
        {
            Ray ray = viewCamera.ScreenPointToRay(screenPosition);
            if (!Physics.Raycast(ray, out RaycastHit hit, 500)) return null;
            SandboxUnitView unit = hit.collider.GetComponentInParent<SandboxUnitView>();
            return unit != null && world.FindUnit(unit.UnitId) != null ? unit.UnitId : null;
        }

        /// <summary>将允许显示的三维位置投影到当前相机屏幕。</summary>
        /// <param name="worldPosition">公开地形或当前可见单位的位置。</param>
        /// <returns>屏幕位置；深度小于零表示在相机后方。</returns>
        public Vector3 WorldToScreen(Vector3 worldPosition) => viewCamera.WorldToScreenPoint(worldPosition);

        /// <summary>放大或缩小当前观察模式。</summary>
        /// <param name="amount">正值放大，负值缩小。</param>
        public void Zoom(float amount)
        {
            distance = Mathf.Clamp(distance - amount, IsFollowing ? 7 : 24, IsFollowing ? 90 : world.MapSize.x * 1.5f);
            ViewChanged?.Invoke();
        }

        /// <summary>切换到当前角色允许的单位跟随。</summary>
        /// <param name="unitId">当前可见单位标识。</param>
        /// <returns>是否找到跟随目标。</returns>
        public bool Follow(string unitId)
        {
            if (world.FindUnit(unitId) == null) return false;
            if (!IsFollowing)
            {
                overviewFocus = focus;
                overviewDistance = distance;
                overviewYaw = yaw;
                overviewPitch = pitch;
            }
            followedId = unitId;
            distance = 16;
            pitch = 32;
            ViewChanged?.Invoke();
            return true;
        }

        /// <summary>恢复进入跟随前的有效总览位置。</summary>
        public void ReturnToOverview()
        {
            if (IsFollowing)
            {
                focus = overviewFocus;
                distance = overviewDistance;
                yaw = overviewYaw;
                pitch = overviewPitch;
            }
            followedId = null;
            ViewChanged?.Invoke();
        }

        /// <summary>定位到单位附近，保持总览观察模式。</summary>
        /// <param name="unitId">当前允许显示的单位。</param>
        public void Locate(string unitId)
        {
            Transform target = world.FindUnit(unitId);
            if (target == null) return;
            ReturnToOverview();
            focus = target.position;
            distance = 40;
            ViewChanged?.Invoke();
        }

        /// <summary>回到固定地图的初始总览。</summary>
        public void ResetOverview()
        {
            CancelPointerGesture();
            followedId = null;
            focus = new Vector3(world.MapSize.x / 2, 0, world.MapSize.y / 2);
            distance = world.MapSize.x * 0.94f;
            pitch = 53;
            yaw = 0;
            ApplyCamera(false);
            ViewChanged?.Invoke();
        }

        /// <summary>处理鼠标、键盘和界面占用，保持相机与业务状态独立。</summary>
        private void Update()
        {
            Mouse mouse = Mouse.current;
            if (mouse == null || viewCamera == null) return;
            Vector2 pointer = mouse.position.ReadValue();
            bool overUi = IsPointerOverUi?.Invoke(pointer) == true
                || pointer.x < 0 || pointer.y < 0 || pointer.x >= Screen.width || pointer.y >= Screen.height;
            if (overUi)
            {
                CancelPointerGesture();
            }
            else
            {
                if (pointerGesture == PointerGesture.None)
                {
                    PointerGesture pressedGesture = mouse.rightButton.wasPressedThisFrame ? PointerGesture.Rotate
                        : mouse.middleButton.wasPressedThisFrame ? PointerGesture.Pan
                        : mouse.leftButton.wasPressedThisFrame ? PointerGesture.SelectOrPan : PointerGesture.None;
                    if (pressedGesture != PointerGesture.None)
                    {
                        pointerGesture = pressedGesture;
                        mouseDownPosition = pointer;
                        previousPointerPosition = pointer;
                        pointerDragged = false;
                    }
                }
                UpdatePointerGesture(mouse, pointer);
                float scroll = mouse.scroll.ReadValue().y;
                if (Mathf.Abs(scroll) > 0.01f && pointerGesture == PointerGesture.None)
                {
                    // Input System 默认跨平台每格归一为 1，保留触控板的小幅连续滚动。
                    float ticks = InputSystem.settings.scrollDeltaBehavior == InputSettings.ScrollDeltaBehavior.KeepPlatformSpecificInputRange
                        && Application.platform == RuntimePlatform.WindowsPlayer ? scroll / 120f : scroll;
                    float factor = Mathf.Exp(-Mathf.Clamp(ticks, -8, 8) * 0.08f);
                    Zoom(distance * (1 - factor));
                }
            }
            Keyboard keyboard = Keyboard.current;
            if (keyboard == null || IsEditingUi?.Invoke() == true) return;
            if (keyboard.rKey.wasPressedThisFrame) ResetOverview();
            if (IsFollowing) return;
            float horizontal = (keyboard.dKey.isPressed || keyboard.rightArrowKey.isPressed ? 1 : 0) - (keyboard.aKey.isPressed || keyboard.leftArrowKey.isPressed ? 1 : 0);
            float vertical = (keyboard.wKey.isPressed || keyboard.upArrowKey.isPressed ? 1 : 0) - (keyboard.sKey.isPressed || keyboard.downArrowKey.isPressed ? 1 : 0);
            Pan(new Vector2(horizontal, vertical) * (Time.unscaledDeltaTime * distance * 0.35f));
        }

        /// <summary>处理按键所属的拖动和释放，小幅单击与拖动选中互斥。</summary>
        /// <param name="mouse">当前输入设备。</param>
        /// <param name="pointer">当前屏幕位置。</param>
        private void UpdatePointerGesture(Mouse mouse, Vector2 pointer)
        {
            if (pointerGesture == PointerGesture.None) return;
            bool held = pointerGesture == PointerGesture.Rotate ? mouse.rightButton.isPressed
                : pointerGesture == PointerGesture.Pan ? mouse.middleButton.isPressed : mouse.leftButton.isPressed;
            float pixelScale = 900f / Mathf.Max(1, Screen.height);
            if (!pointerDragged && Vector2.Distance(pointer, mouseDownPosition) * pixelScale >= 6)
                pointerDragged = true;
            if (pointerDragged)
            {
                if (pointerGesture == PointerGesture.Rotate)
                {
                    Vector2 movement = (pointer - previousPointerPosition) * pixelScale;
                    yaw = Mathf.Repeat(yaw + movement.x * 0.2f, 360);
                    pitch = Mathf.Clamp(pitch - movement.y * 0.12f, 18, 78);
                }
                else
                {
                    if (IsFollowing)
                    {
                        // 用户开始手动平移时保留当前观察中心，退出跟随而不跳回旧总览。
                        followedId = null;
                        distance = Mathf.Clamp(distance, 24, world.MapSize.x * 1.5f);
                        ViewChanged?.Invoke();
                    }
                    PanBetweenScreenPoints(previousPointerPosition, pointer);
                }
            }
            previousPointerPosition = pointer;
            if (held) return;
            if (pointerGesture == PointerGesture.SelectOrPan && !pointerDragged)
            {
                if (IsMapCommand?.Invoke() == true || Keyboard.current?.shiftKey.isPressed == true)
                { if (TryMapPoint(pointer, out Vector3 point)) MapClicked?.Invoke(point); }
                else UnitSelected?.Invoke(PickUnit(pointer));
            }
            CancelPointerGesture();
        }

        /// <summary>将屏幕拖动投影到观察中心高度的水平面，使地图跟随鼠标方向移动。</summary>
        /// <param name="previous">上一帧屏幕位置。</param>
        /// <param name="current">当前屏幕位置。</param>
        private void PanBetweenScreenPoints(Vector2 previous, Vector2 current)
        {
            var plane = new Plane(Vector3.up, focus);
            Ray previousRay = viewCamera.ScreenPointToRay(previous);
            Ray currentRay = viewCamera.ScreenPointToRay(current);
            if (!plane.Raycast(previousRay, out float previousDistance) || !plane.Raycast(currentRay, out float currentDistance)) return;
            Vector3 movement = previousRay.GetPoint(previousDistance) - currentRay.GetPoint(currentDistance);
            focus += new Vector3(movement.x, 0, movement.z);
            ClampFocus();
        }

        /// <summary>清除未完成手势，防止失焦或跨越界面后继续拖动和误选中。</summary>
        private void CancelPointerGesture()
        {
            pointerGesture = PointerGesture.None;
            pointerDragged = false;
        }

        /// <summary>窗口失焦时释放当前地图手势。</summary>
        /// <param name="focused">窗口是否保持焦点。</param>
        private void OnApplicationFocus(bool focused)
        {
            if (!focused) CancelPointerGesture();
        }

        /// <summary>观察组件被禁用时不保留按下状态。</summary>
        private void OnDisable() => CancelPointerGesture();

        /// <summary>在渲染帧末跟随已收到的有效位置，不外推业务行动。</summary>
        private void LateUpdate()
        {
            if (IsFollowing)
            {
                Transform target = world.FindUnit(followedId);
                if (target == null) ReturnToOverview();
                else focus = target.position;
            }
            ApplyCamera(true);
        }

        /// <summary>沿地图平面移动观察中心并限制范围。</summary>
        private void Pan(Vector2 movement)
        {
            Quaternion heading = Quaternion.Euler(0, yaw, 0);
            focus += heading * new Vector3(movement.x, 0, movement.y);
            ClampFocus();
        }

        /// <summary>将观察中心限制在公开地图范围内。</summary>
        private void ClampFocus()
        {
            focus.x = Mathf.Clamp(focus.x, 0, world.MapSize.x);
            focus.z = Mathf.Clamp(focus.z, 0, world.MapSize.y);
        }

        /// <summary>根据当前观察参数平滑设置相机。</summary>
        private void ApplyCamera(bool smooth)
        {
            Quaternion rotation = Quaternion.Euler(pitch, yaw, 0);
            Vector3 position = focus - rotation * Vector3.forward * distance;
            float blend = smooth ? 1 - Mathf.Exp(-Time.unscaledDeltaTime * 9) : 1;
            transform.position = Vector3.Lerp(transform.position, position, blend);
            transform.rotation = Quaternion.Slerp(transform.rotation, rotation, blend);
        }
    }
}
