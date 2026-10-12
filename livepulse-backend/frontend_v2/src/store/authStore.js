import { create } from "zustand";

/**
 * Auth store — JWT + user profile.
 *
 * Note: localStorage key 跟旧 frontend 一致（wb_auth_token / wb_auth_user）。
 * 这是有意为之：用户在旧 UI 登录的状态，切到 v2 仍然有效；
 * 反过来同理。两套 UI 可共享登录态，便于切换验证。
 */

const LS_TOKEN_KEY = "wb_auth_token";
const LS_USER_KEY = "wb_auth_user";

function loadFromStorage() {
  try {
    const token = localStorage.getItem(LS_TOKEN_KEY);
    const userRaw = localStorage.getItem(LS_USER_KEY);
    const user = userRaw ? JSON.parse(userRaw) : null;
    return { token, user };
  } catch {
    return { token: null, user: null };
  }
}

const initial = loadFromStorage();

const useAuthStore = create((set, get) => ({
  token: initial.token,
  user: initial.user,
  bootChecked: false,

  setToken: (token) => {
    if (token) {
      localStorage.setItem(LS_TOKEN_KEY, token);
    } else {
      localStorage.removeItem(LS_TOKEN_KEY);
    }
    set({ token });
  },

  setUser: (user) => {
    if (user) {
      localStorage.setItem(LS_USER_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(LS_USER_KEY);
    }
    set({ user });
  },

  markBootChecked: () => set({ bootChecked: true }),

  logout: () => {
    localStorage.removeItem(LS_TOKEN_KEY);
    localStorage.removeItem(LS_USER_KEY);
    set({ token: null, user: null });
  },
}));

export default useAuthStore;
