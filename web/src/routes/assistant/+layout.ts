// Страница /assistant доступна любому auth-user (root layout делает redirect
// на /login для нелогинов). Admin может выбрать юзера через dropdown, user
// видит только свой конфиг. Backend self-or-admin guard — вторая линия защиты.
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async () => {
	return {};
};
