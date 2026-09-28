# Phoenix LiveView 대비 기능 갭 분석

> django-wireview가 Phoenix LiveView 수준에 도달하기 위해 필요한 기능 목록
>
> **최종 업데이트**: 2026-09-27

---

## 개요

아래 2절의 비교표 114행 기준이다. 어림수가 아니라 표를 센 값이고, `tests/test_feature_gap.py`가
표를 다시 세어 이 숫자와 비교한다.

| 상태 | 행 |
|------|---:|
| ✅ 지원 | 108 |
| 🟡 부분 지원 | 1 |
| 🟠 미지원 (전부 GAP 번호와 이슈가 있다) | 3 |
| ⚪ 설계상 제외 | 2 |

✅ 중 6행은 Phoenix에 없는 wireview 고유 기능이다(상태 칸이 `✅ 추가 기능`인 행). 🟠 행은 3절 표에서
GAP 번호로 추적한다.

### ✅의 근거

2절 표의 마지막 칸은 그 행의 기능을 **실제로 실행하는** 테스트의 pytest 노드 id다
(`tests/test_x.py::test_y`, 여럿이면 `<br>`로 구분). ✅ 행은 근거가 있어야 하고, 적힌 테스트가
존재해야 한다 — `tests/test_feature_gap.py`가 둘 다 검사한다. 근거로 인정하는 것:

- 기능의 **공개 경로**를 탄다. 내부 함수를 직접 부르는 테스트는 근거가 아니다. on_mount 훅(#75)은
  훅 실행 함수가 테스트돼 있었지만 그 함수를 부르는 곳이 없어 한 번도 돌지 않았다.
- 브라우저가 있어야 의미가 있는 기능(JS 명령, 클라이언트 훅, 로딩 클래스)은 E2E가 근거다.
- 다중 프로세스나 채널 레이어가 핵심인 기능은 그 조건을 만드는 테스트가 근거다.
- 근거 테스트는 기능 코드를 되돌리면 실패해야 한다.

근거를 댈 수 없는 기능은 ✅를 붙이지 않는다.

---

## 1. 기능 커버리지 현황

### 카테고리별 상태

| 카테고리 | 커버리지 | 상태 |
|----------|:--------:|------|
| Core Lifecycle | 95% | ✅ 대부분 완료 |
| Real-time (PubSub, Presence) | 95% | ✅ 완료 |
| JS Commands (LiveView.JS) | 95% | ✅ 완료 |
| Optimistic UI | 95% | ✅ 완료 |
| Streams | 80% | ✅ 기본 완료 |
| File Uploads | 100% | ✅ 완료 |
| Async Operations | 95% | ✅ 대부분 완료 |
| Navigation | 85% | ✅ 대부분 완료 |
| **JavaScript Hooks** | 95% | ✅ 완료 |
| **Components (Slots, Function, Live)** | 95% | ✅ 완료 |
| Testing | 80% | ✅ 기본 완료 |
| Developer Tools | 85% | ✅ 대부분 완료 |

---

## 2. 상세 기능 비교

### 2.1 Core Lifecycle ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| mount/joined | `mount/3` | `joined()` | ✅ | `tests/test_lifecycle_hooks.py::TestWebSocketJoin::test_a_rejoin_replaces_the_instance_so_the_hooks_run_again`<br>`tests/test_errors.py::test_a_join_that_raises_is_marked_not_retried` |
| handle_event | `handle_event/3` | 메서드 직접 호출 | ✅ | `tests/test_event_refs.py::test_the_answer_carries_the_events_ref_whether_or_not_it_changed_anything`<br>`tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_form_submit_delivers_named_inputs_as_handler_arguments` |
| handle_info | `handle_info/2` | `notification()` | ✅ | `tests/test_connections.py::test_a_broadcast_from_one_connection_rerenders_another`<br>`tests/test_live_component_render.py::test_a_child_that_appears_through_a_broadcast_is_joined` |
| handle_params | `handle_params/3` | `params_changed()` | ✅ | `tests/test_live_component_render.py::test_a_child_that_appears_through_params_changed_is_joined`<br>`tests/test_attach_hook.py::test_a_handle_params_hook_runs_before_params_changed` |
| terminate | `terminate/2` | `leaving()` | ✅ | `tests/test_leave.py::test_leave_calls_leaving_and_removes_the_component`<br>`tests/test_connections.py::test_closing_the_socket_calls_leaving` |
| ORM mutation | - | `mutation()` | ✅ 추가 기능 | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_stream_insert_reaches_the_dom`<br>`tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_toggle_and_delete_round_trip` |
| 세션 접근 | `mount/3`의 session | `self.session` (읽기 전용) | ✅ GAP-029 | `tests/test_session.py::test_connect_snapshots_the_session_so_reads_cost_nothing`<br>`tests/test_session.py::test_the_dead_render_reads_the_request_session` |

### 2.2 Real-time Features ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| PubSub broadcast | `Phoenix.PubSub` | `self.broadcast()` · `abroadcast()` · `broadcast()` | ✅ | `tests/test_connections.py::test_a_broadcast_from_one_connection_rerenders_another`<br>`tests/test_connections.py::test_the_module_broadcast_reaches_a_connection_from_sync_code` |
| Presence tracking | `Phoenix.Presence` | `PresenceMixin` | ✅ | `tests/test_connections.py::test_presence_join_typing_and_leave_reach_the_tracker` |
| Presence list | `Presence.list/1` | `presence_users` | ✅ | `tests/test_connections.py::test_presence_join_typing_and_leave_reach_the_tracker` |
| Typing indicators | 수동 구현 | `presence_set_typing()` | ✅ | `tests/test_connections.py::test_presence_join_typing_and_leave_reach_the_tracker`<br>`tests/test_presence.py::TestPresenceMixin::test_typing_auto_timeout` |
| Auto-broadcast (ORM) | 수동 구현 | `AUTO_BROADCAST` | ✅ 추가 기능 | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_stream_insert_reaches_the_dom`<br>`tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_toggle_and_delete_round_trip` |

### 2.3 LiveView.JS (Client Commands) ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| show/hide/toggle | ✅ | `JS().show/hide/toggle()` | ✅ | `tests/test_js_commands_e2e.py::test_show_and_hide`<br>`tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy` |
| add_class/remove_class | ✅ | `JS().add_class/remove_class()` | ✅ | `tests/test_js_commands_e2e.py::test_add_and_remove_class` |
| toggle_class | ✅ | `JS().toggle_class()` | ✅ | `tests/test_js_commands_e2e.py::test_toggle_class` |
| set_attribute | ✅ | `JS().set_attr()` | ✅ | `tests/test_js_commands_e2e.py::test_set_and_remove_attribute` |
| remove_attribute | ✅ | `JS().remove_attr()` | ✅ | `tests/test_js_commands_e2e.py::test_set_and_remove_attribute` |
| transition | ✅ | `JS().transition()` | ✅ | `tests/test_js_commands_e2e.py::test_transition_adds_its_classes_for_the_duration` |
| focus/focus_first | ✅ | `JS().focus/focus_first()` | ✅ | `tests/test_js_commands_e2e.py::test_focus`<br>`tests/test_js_commands_e2e.py::test_focus_first_skips_what_cannot_take_focus` |
| push (server event) | ✅ | `JS().push()` | ✅ | `tests/test_input_values_e2e.py::test_enter_through_a_js_push_empties_the_field_like_a_handler_binding`<br>`tests/test_js_commands_e2e.py::test_a_chain_runs_every_command_in_order` |
| dispatch (DOM event) | ✅ | `JS().dispatch()` | ✅ | `tests/test_js_commands_e2e.py::test_dispatch_fires_a_dom_event_with_its_detail` |
| navigate | ✅ | `JS().navigate()` | ✅ | `tests/test_js_commands_e2e.py::test_navigate_pushes_a_history_entry`<br>`tests/test_js_commands_e2e.py::test_navigate_with_replace_goes_there_in_place_of_this_entry` |
| Command chaining | ✅ | ✅ 지원 | ✅ | `tests/test_js_commands_e2e.py::test_a_chain_runs_every_command_in_order` |

### 2.4 Optimistic UI ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| phx-click-loading | ✅ | `wireview-click-loading` | ✅ | `tests/test_js_commands_e2e.py::test_a_click_marks_its_element_loading_until_the_answer`<br>`tests/test_js_commands_e2e.py::test_a_binding_on_the_component_root_is_cleared_too` |
| phx-submit-loading | ✅ | `wireview-submit-loading` | ✅ | `tests/test_js_commands_e2e.py::test_a_submit_marks_the_form_loading_until_the_answer` |
| phx-change-loading | ✅ | `wireview-change-loading` | ✅ | `tests/test_js_commands_e2e.py::test_a_change_marks_its_element_loading_until_the_answer` |
| phx-disable-with | ✅ | `wire-disabled-with` | ✅ | `tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy`<br>`tests/test_js_commands_e2e.py::test_the_submit_button_of_a_form_is_disabled_with_its_text`<br>`tests/test_js_commands_e2e.py::test_a_chain_that_pushes_is_disabled_with_its_text` |
| Client-side immediate | ✅ | JS() 명령어 | ✅ | `tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy`<br>`tests/test_js_commands_e2e.py::test_a_chain_that_pushes_is_disabled_with_its_text` |

### 2.5 Streams ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| stream() | ✅ | `stream()` | ✅ | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_filter_switch_resets_the_stream`<br>`tests/test_streams_e2e.py::test_dom_id_names_each_item` |
| stream_insert() | ✅ | `stream_insert()` | ✅ | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_stream_insert_reaches_the_dom`<br>`tests/test_streams_e2e.py::test_scrolling_to_the_bottom_loads_more` |
| stream_delete() | ✅ | `stream_delete()` | ✅ | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_toggle_and_delete_round_trip`<br>`tests/test_streams.py::TestComponentStreamMethods::test_stream_delete_with_int_id` |
| DOM ID generation | ✅ | `dom_id=` 함수 (기본 `{name}-{pk}`) | ✅ | `tests/test_streams_e2e.py::test_dom_id_names_each_item`<br>`tests/test_streams.py::TestComponentStreamMethods::test_stream_delete_with_int_id` |
| wire-stream attribute | `phx-update="stream"` | `wire-stream` | ✅ 재렌더에서 내용이 보존된다 | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_stream_insert_reaches_the_dom`<br>`tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_filter_switch_resets_the_stream` |
| 같은 dom id 재삽입 | 제자리 갱신 | 제자리 갱신 | ✅ | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_toggle_and_delete_round_trip` |
| stream :limit | ✅ | `stream(limit=N)` | ✅ | `tests/test_streams_e2e.py::test_limit_keeps_only_the_newest` |
| stream :reset | ✅ | `stream()`이 곧 reset이다 | ✅ | `tests/testproj/bookmarks/tests.py::TestBookmarksE2E::test_filter_switch_resets_the_stream`<br>`tests/testproj/bookmarks/tests.py::test_filter_unread_excludes_read_items` |
| phx-viewport-top/bottom | ✅ | `wire-viewport-*` ([튜토리얼](./tutorials/06-streams-api.md)) | ✅ | `tests/test_streams_e2e.py::test_scrolling_to_the_bottom_loads_more`<br>`tests/test_streams_e2e.py::test_scrolling_back_to_the_top_calls_the_top_binding` |

### 2.6 File Uploads ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| allow_upload() | ✅ | `allow_upload()` | ✅ | `tests/test_uploads_e2e.py::test_one_upload_after_another_past_max_entries`<br>`tests/test_uploads.py::TestComponentUploadMethods::test_allow_upload_sends_config_message` |
| live_file_input | ✅ | `{% upload_input %}` | ✅ | `tests/test_uploads_e2e.py::test_one_upload_after_another_past_max_entries`<br>`tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy` |
| Progress tracking | ✅ | `entry.progress` | ✅ | `tests/test_upload_render.py::test_progress_is_rendered`<br>`tests/test_distributed_uploads.py::test_progress_from_another_worker_updates_the_entry` |
| Image preview | ✅ | `{% upload_preview %}` | ✅ | `tests/test_uploads_e2e.py::test_the_preview_shows_the_chosen_image_before_it_is_uploaded` |
| Drag and drop | ✅ | `{% upload_drop_zone %}` | ✅ | `tests/test_uploads_e2e.py::test_the_drop_zone_uploads_what_is_dropped` |
| Chunk upload | ✅ | ✅ | ✅ | `tests/test_multiworker_uploads.py::test_the_chunks_go_to_the_other_worker`<br>`tests/test_uploads.py::TestUploadView::test_the_last_chunk_completes_the_upload` |
| consume_uploads | ✅ | `consume_uploads()` | ✅ | `tests/test_uploads_e2e.py::test_one_upload_after_another_past_max_entries`<br>`tests/test_uploads.py::TestComponentUploadMethods::test_an_upload_read_in_the_loop_is_consumed` |
| External upload (S3) | ✅ | `external=callback` | ✅ | `tests/test_uploads_e2e.py::test_an_external_upload_goes_to_the_presigned_url`<br>`tests/test_upload_render.py::test_an_async_external_callback_is_awaited` |
| Magic byte validation | ✅ | ✅ | ✅ | `tests/test_uploads.py::TestUploadView::test_content_that_does_not_match_the_extension_is_refused` |

### 2.7 Async Operations ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| assign_async() | ✅ | `assign_async()` | ✅ | `tests/test_async_result.py::TestAssignAsync::test_updates_to_success_after_completion`<br>`tests/test_async_result.py::TestAssignAsync::test_triggers_rerender_on_completion` |
| AsyncResult states | loading/ok/failed | loading/ok/failed | ✅ | `tests/test_async_result.py::TestAssignAsync::test_returns_loading_state_immediately`<br>`tests/test_async_result.py::TestAssignAsync::test_updates_to_error_on_failure` |
| start_async() | ✅ | `start_async()` | ✅ | `tests/test_start_async.py::test_the_result_reaches_handle_async_and_renders`<br>`tests/test_async_lifetime.py::test_a_replaced_task_leaves_its_replacement_tracked` |
| cancel_async() | ✅ | `cancel_async()` | ✅ | `tests/test_start_async.py::test_cancel_async_stops_the_operation_before_handle_async` |
| handle_async() | ✅ | `handle_async()` | ✅ | `tests/test_start_async.py::test_the_result_reaches_handle_async_and_renders`<br>`tests/test_start_async.py::test_a_failed_operation_reaches_handle_async_as_exit`<br>`tests/test_start_async.py::test_a_handle_async_that_raises_joins_the_component_again` |

### 2.8 Navigation ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| push_navigate | ✅ | `self.wire.redirect_to()` | ✅ | `tests/test_js_commands_e2e.py::test_redirect_to_navigates`<br>`tests/test_lifecycle_hooks.py::TestWebSocketJoin::test_a_redirecting_hook_sends_url_change_and_no_render` |
| push_patch | ✅ | `self.wire.push_to()` | ✅ | `tests/test_js_commands_e2e.py::test_push_to_adds_an_entry_and_runs_params_changed`<br>`tests/test_live_session_e2e.py::TestBoundaryNavigation::test_a_server_push_out_of_the_boundary_reloads` |
| replace | ✅ | `self.wire.replace_to()` | ✅ | `tests/test_js_commands_e2e.py::test_replace_to_changes_the_url_in_place_and_runs_params_changed`<br>`tests/test_params_changed.py::TestAQueryOnlyDestination::test_replace_keeps_it_literal` |
| handle_params | ✅ | `params_changed()` | ✅ | `tests/test_js_commands_e2e.py::test_push_to_adds_an_entry_and_runs_params_changed`<br>`tests/test_live_component_render.py::test_a_child_that_appears_through_params_changed_is_joined` |
| live_session | ✅ | `live_session()` + `@session.view` | ✅ (GAP-009. 경계는 페이지 단위다 — Django 뷰가 라우트이기 때문. [문서](./features/live-session.md)) | `tests/test_live_session_e2e.py::TestBoundaryNavigation::test_a_link_click_out_of_the_boundary_reloads`<br>`tests/test_live_session.py::TestValidSignaturesDoNotCombine::test_a_state_from_another_boundary_is_refused` |
| Client-side boost | ✅ | `WIREVIEW["BOOST_PAGES"]` | ✅ | `tests/test_live_session_e2e.py::TestBoundaryNavigation::test_a_boosted_move_inside_the_boundary_still_morphs` |

### 2.9 JavaScript Interoperability ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| **wire-hook** | ✅ 라이프사이클 훅 | `wire-hook="Name"` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_a_registered_hook_mounts_and_rewrites_its_element` |
| Hook.mounted | ✅ | `mounted()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_a_registered_hook_mounts_and_rewrites_its_element`<br>`examples/hooks/tests.py::TestHookLifecycle::test_putting_it_back_mounts_a_new_instance` |
| Hook.updated | ✅ | `updated()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_a_morph_runs_beforeupdate_then_updated`<br>`tests/test_offline_e2e.py::test_a_hook_keeps_its_callbacks_after_the_reconnect` |
| Hook.destroyed | ✅ | `destroyed()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_removing_the_element_destroys_the_hook` |
| Hook.disconnected | ✅ | `disconnected()` | ✅ | `tests/test_offline_e2e.py::test_a_hook_hears_the_disconnect_and_the_reconnect` |
| Hook.reconnected | ✅ | `reconnected()` | ✅ | `tests/test_offline_e2e.py::test_a_hook_hears_the_disconnect_and_the_reconnect` |
| Hook.beforeUpdate | ✅ | `beforeUpdate()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_a_morph_runs_beforeupdate_then_updated` |
| pushEvent (client→server) | ✅ | `this.pushEvent()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_the_hook_pushes_an_event_and_reads_the_answer`<br>`tests/test_offline_e2e.py::test_a_hook_keeps_its_callbacks_after_the_reconnect` |
| handleEvent (server→client) | ✅ | `this.handleEvent()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_the_server_pushes_an_event_the_hook_is_listening_for`<br>`tests/test_offline_e2e.py::test_a_hook_keeps_its_callbacks_after_the_reconnect` |
| handle_hook_event (server) | - | `handle_hook_event()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_the_hook_pushes_an_event_and_reads_the_answer`<br>`tests/test_live_session_contract.py::TestARefusedIdAnswersNothing::test_the_control_reaches_an_admitted_component` |
| push_event (server→client) | ✅ | `push_event()` | ✅ | `examples/hooks/tests.py::TestHookLifecycle::test_the_server_pushes_an_event_the_hook_is_listening_for`<br>`examples/hooks/tests.py::test_highlight_pushes_to_every_hook_of_this_component` |
| Colocated hooks | ✅ | 앱의 `static/<app_label>/hooks/*.js` | ✅ (GAP-032) | `tests/test_hook_collection.py::TestWhatTheHeaderEmits::test_every_hook_file_gets_a_deferred_script`<br>`examples/hooks/tests.py::TestHookLifecycle::test_a_slow_hook_file_still_registers_before_the_first_join` |
| onBeforeElUpdated | ✅ | `wireview.dom.onBeforeElUpdated()` | ✅ | `tests/test_js_commands_e2e.py::test_on_before_el_updated_keeps_it`<br>`tests/test_js_commands_e2e.py::test_a_render_drops_what_javascript_added_to_an_element` |

### 2.10 Components ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| Stateful component | ✅ | ✅ Component | ✅ | `tests/test_testing.py::TestMountedComponent::test_call_multiple_handlers`<br>`tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy` |
| **LiveComponent** | ✅ 중첩 상태 | `{% live_component %}` | ✅ | `tests/test_live_component_render.py::test_join_sends_one_render_frame_with_the_children_inside`<br>`examples/livecomp/tests.py::TestLiveComponentE2E::test_independent_counter_state` |
| **Function components** | ✅ | `@function_component` + `{% func %}` | ✅ | `tests/test_function_component.py::TestFuncTemplateTag::test_simple_func_tag`<br>`tests/test_function_component.py::TestFuncTemplateTag::test_func_tag_with_context_variable` |
| **Slots (named)** | ✅ `<:header>` | `{% fill header %}` | ✅ | `tests/test_slots_integration.py::TestComponentBlockTag::test_component_block_with_multiple_fills`<br>`tests/test_live_component_slots.py::test_slot_content_renders_inside_the_child_and_stays_out_of_the_parent_diff` |
| Slots (default) | ✅ `inner_block` | `{% render_slot %}` | ✅ | `tests/test_slots_integration.py::TestRenderSlotTag::test_render_slot_default`<br>`tests/test_live_component_slots.py::test_slot_content_renders_inside_the_child_and_stays_out_of_the_parent_diff` |
| Slots (let binding) | ✅ | `let:item` | ✅ | `tests/test_slots_integration.py::TestFillTag::test_fill_with_let_binding`<br>`tests/test_live_component_slots.py::test_a_let_fill_renders_with_the_values_render_slot_passes` |
| Slots in LiveComponent | ✅ `<:slot>` in live_component | `{% live_component_block %}` | ✅ | `tests/test_live_component_slots.py::test_slot_content_renders_inside_the_child_and_stays_out_of_the_parent_diff`<br>`tests/test_live_component_slots.py::test_the_childs_own_event_keeps_the_slot_content` |
| @myself target | ✅ | `myself=True` | ✅ | `examples/livecomp/tests.py::TestLiveComponentE2E::test_increment_counter_with_myself_targeting` |
| update/2 callback | ✅ | `update()` | ✅ | `tests/test_live_component_render.py::test_a_changed_prop_updates_and_rerenders_only_the_children_it_reaches`<br>`examples/livecomp/tests.py::TestLiveComponentE2E::test_parent_sync_all_updates_children` |
| update_many/1 | ✅ 배치 최적화 | ❌ | 🟠 GAP-035 ([#74](https://github.com/itda-work/django-wireview/issues/74)) |  |
| Nested LiveViews | ✅ 프로세스 격리 | LiveComponent (같은 프로세스) | ⚪ 설계상 제외 |  |

Nested LiveViews를 제외로 두는 이유: Phoenix의 중첩 LiveView는 BEAM 프로세스 격리에서 오는
성질이다 — 자식이 죽어도 부모가 살고, 자식마다 자기 메일박스와 스케줄링을 가진다. Django·ASGI에는
그 단위가 없고, 연결 하나가 곧 컨슈머 하나다. wireview는 같은 프로세스 안의 `LiveComponent`로
합성 요구를 받고, 격리 요구는 별도 연결(별도 페이지)로 받는다. 이 선을 옮기려면 GAP-027(#60)의
세션 분리가 먼저다.

### 2.11 Form Handling ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| phx-change | ✅ | `{% on "input" %}` | ✅ | `tests/test_input_values_e2e.py::test_typing_continues_through_the_answer_to_its_own_input_event`<br>`tests/test_input_values_e2e.py::test_an_earlier_events_answer_does_not_take_the_enter_mark` |
| phx-submit | ✅ | `{% on "submit" %}` | ✅ | `tests/test_input_values_e2e.py::test_a_submit_still_empties_its_form`<br>`tests/test_forms_e2e.py::test_a_valid_django_form_saves` |
| phx-debounce | ✅ | `.debounce.N` | ✅ | `tests/test_forms_e2e.py::test_debounce_sends_once_after_the_typing_stops`<br>`tests/test_csp_e2e.py::test_every_binding_works_under_a_strict_policy` |
| phx-throttle | ✅ | `.throttle.N` | ✅ | `tests/test_forms_e2e.py::test_throttle_lets_one_through_per_window` |
| phx-feedback-for | ✅ | `wire-feedback-for` | ✅ | `tests/test_forms_e2e.py::test_feedback_waits_for_the_field_to_be_touched`<br>`tests/test_forms_e2e.py::test_a_submit_shows_every_fields_feedback` |
| phx-auto-recover | ✅ | `wire-auto-recover` | ✅ (GAP-008) | `tests/test_offline_e2e.py::test_auto_recover_sends_the_forms_values_to_its_handler`<br>`tests/test_offline_e2e.py::test_auto_recover_without_a_handler_replays_the_forms_change_binding` |
| Form recovery | ✅ 자동 | 재연결 시 폼 상태 복원 | ✅ (GAP-008) | `tests/test_offline_e2e.py::test_auto_recover_sends_the_forms_values_to_its_handler`<br>`tests/test_offline_e2e.py::test_auto_recover_without_a_handler_replays_the_forms_change_binding` |
| Changeset integration | Ecto | 핸들러에서 Django Form으로 검증 ([문서](./features/form-feedback.md)) | ✅ 다른 접근 | `tests/test_forms_e2e.py::test_a_valid_django_form_saves`<br>`tests/test_forms_e2e.py::test_feedback_waits_for_the_field_to_be_touched` |

### 2.12 Performance Features ⚠️

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| HTML Diff | ✅ 바이너리 | ✅ Phoenix 스타일 (GAP-024로 부분 diff 실동작) | ✅ | `tests/test_diff_stability.py::test_live_render_diff_is_partial_after_state_change`<br>`tests/test_live_component_render.py::test_a_changed_prop_updates_and_rerenders_only_the_children_it_reaches` |
| skip_render | ✅ | `skip_render()` | ✅ | `tests/test_render_control.py::test_skip_render_answers_without_a_diff_and_the_next_render_catches_up` |
| force_render | ✅ | `force_render()` | ✅ | `tests/test_render_control.py::test_force_render_sends_a_full_render_with_nothing_changed` |
| **temporary_assigns** | ✅ | `Meta.temporary_assigns` | 🟡 다음 렌더가 초기화된 필드를 화면에서 비운다 ([#111](https://github.com/itda-work/django-wireview/issues/111)) | `tests/test_render_control.py::test_temporary_assigns_are_cleared_after_a_live_render`<br>`tests/test_render_control.py::test_the_next_render_empties_a_cleared_temporary_assign` |
| Sticky components | ✅ | ❌ | 🟠 GAP-033 ([#72](https://github.com/itda-work/django-wireview/issues/72)) |  |
| Comprehensions | ✅ 키 기반 | ✅ 내용 기반 짝짓기 (GAP-025, GAP-030). 템플릿 키 없이 이동·삽입·삭제가 그 항목만의 페이로드 | ✅ | `tests/test_comprehension_moves.py::test_a_client_that_names_the_version_gets_moves`<br>`tests/test_comprehension_moves_e2e.py::test_moving_rows_ends_in_the_same_dom_with_either_form` |

### 2.13 Testing ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| render_component | ✅ | `mount()` | ✅ | `tests/test_testing.py::TestMount::test_mount_with_initial_state`<br>`examples/slots/tests.py::test_a_card_renders_without_any_fill` |
| render_click | ✅ | `call()` | ✅ | `tests/test_testing.py::TestMountedComponent::test_call_handler_with_args`<br>`tests/test_testing.py::TestCallMeetsTheEventChecks::test_a_private_or_framework_method_is_refused` |
| render_change | ✅ | `call()` | ✅ | `tests/test_testing.py::TestCallMeetsTheEventChecks::test_arguments_the_handler_does_not_take_are_dropped`<br>`examples/search/tests.py::test_a_query_matches_title_or_author` |
| assert_patch | ✅ | `assert_pushed_to()` · `assert_replaced_to()` · `follow_push()` | ✅ (GAP-031) | `tests/test_navigation_helpers.py::TestAssertingWhereItNavigated::test_it_does_not_confuse_the_three_commands`<br>`tests/test_navigation_helpers.py::TestFollowingAPush::test_it_runs_params_changed` |
| follow_redirect | ✅ | `follow_redirect()` | ✅ (GAP-031) | `tests/test_navigation_helpers.py::TestFollowingARedirect::test_it_mounts_the_destination`<br>`tests/test_navigation_helpers.py::TestFollowingARedirect::test_a_refusing_destination_refuses_here_too` |
| 스트림 검사 | - | `stream_html()` · `stream_items()` · `stream_ops()` | ✅ 추가 기능 | `tests/test_navigation_helpers.py::TestStreamHelpers::test_it_collects_the_html`<br>`tests/test_navigation_helpers.py::TestStreamHelpers::test_it_exposes_the_operations` |
| MockChannelLayer | - | `mount()`이 채널 레이어를 흉내 내고 `view.wire.broadcasts`에 남긴다 | ✅ 추가 기능 | `tests/test_presence.py::TestPresenceMixin::test_presence_join_broadcasts`<br>`examples/notifications/tests.py::test_dismissing_deletes_and_tells_the_bell` |

### 2.14 Developer Tools ✅

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| enableDebug | ✅ | `wireview.debug.enable()` | ✅ | `tests/test_js_commands_e2e.py::test_debug_enable_logs_what_the_socket_carries_until_disabled` |
| enableLatencySim | ✅ | `wireview.debug.latency()` | ✅ | `tests/test_js_commands_e2e.py::test_debug_latency_holds_back_what_the_page_sends` |
| enableProfiling | ✅ | `wireview.debug.enableProfiling()` | ✅ | `tests/test_js_commands_e2e.py::test_debug_profiling_counts_events_and_patches` |
| Telemetry | ✅ | `wireview.telemetry` 시그널 | ✅ (GAP-022) | `tests/test_telemetry.py::test_event_handling_is_measured`<br>`tests/test_telemetry.py::test_render_and_diff_are_measured_separately` |
| **Type Stubs** | - | `wireview_stubs` | ✅ 추가 기능 | `tests/test_stubs.py::TestTheCommand::test_it_writes_a_stub_whose_handlers_are_what_a_client_can_call`<br>`tests/test_stubs.py::TestTheCommand::test_check_fails_when_a_stub_is_stale` |
| **LSP Metadata** | - | `wireview_lsp` | ✅ 추가 기능 | `tests/test_lsp_metadata.py::TestWireviewLspCommand::test_component_metadata`<br>`tests/test_lsp_metadata.py::test_is_handler_says_what_a_client_can_call` |

### 2.15 Miscellaneous ⚠️

| 기능 | Phoenix LiveView | django-wireview | 상태 | 근거 |
|------|:----------------:|:---------------:|:----:|------|
| Page title | ✅ `assign(:page_title)` | `push_title()` | ✅ | `tests/test_js_commands_e2e.py::test_push_title_sets_the_documents_title`<br>`tests/test_session_commands.py::test_title_and_flash_reach_the_client` |
| Flash messages | ✅ `put_flash` | `put_flash()` | ✅ | `tests/test_js_commands_e2e.py::test_put_flash_shows_a_dismissible_message`<br>`tests/test_session_commands.py::test_title_and_flash_reach_the_client` |
| Dead views | ✅ JS 비활성화 폴백 | ❌ | 🟠 GAP-034 ([#73](https://github.com/itda-work/django-wireview/issues/73)) |  |
| LongPolling fallback | ✅ | ❌ | ⚪ 설계상 제외 (GAP-012, [설계 메모](./design/longpolling-fallback.md)) |  |
| on_mount hooks | ✅ | `Meta.on_mount` | ✅ (GAP-021. 호출부가 없어 훅이 실행되지 않던 것을 [#75](https://github.com/itda-work/django-wireview/issues/75)에서 붙였다) | `tests/test_lifecycle_hooks.py::TestWebSocketJoin::test_a_redirecting_hook_sends_url_change_and_no_render`<br>`tests/test_lifecycle_hooks.py::TestHttpRender::test_the_page_component_runs_its_hooks` |
| attach_hook | ✅ | `attach_hook()` | ✅ | `tests/test_attach_hook.py::test_a_halting_handle_event_hook_stops_the_handler`<br>`tests/test_attach_hook.py::test_a_handle_params_hook_runs_before_params_changed`<br>`tests/test_attach_hook.py::test_an_after_render_hook_runs_after_each_render` |

---

## 3. 미구현 기능 우선순위

### 🔴 P0: Critical (DX에 큰 영향)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-001~~ | ~~**JavaScript Hooks**~~ | ~~클라이언트 측 라이프사이클 훅 (`wire-hook`)~~ | ~~상~~ | ✅ 완료 |
| ~~GAP-002~~ | ~~**Slots**~~ | ~~컴포넌트 콘텐츠 합성 (`{% fill %}`, `{% render_slot %}`)~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-003~~ | ~~**Function Components**~~ | ~~상태 없는 재사용 가능 템플릿 함수~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-004~~ | ~~**handle_params**~~ | ~~URL 파라미터 변경 시 콜백~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-005~~ | ~~**LiveComponent**~~ | ~~독립 상태를 가진 중첩 컴포넌트~~ | ~~상~~ | ✅ 완료 |
| GAP-006 | **temporary_assigns** | 렌더 후 메모리 자동 해제. 다음 렌더가 화면에서 비우지 않게 | 중 | 🟡 [#111](https://github.com/itda-work/django-wireview/issues/111) |
| ~~GAP-024~~ | ~~**Stable HTML Diff**~~ | ~~서명 상태를 dynamic 파트로 옮겨 부분 diff 활성화, 상태 압축 서명~~ | ~~하~~ | ✅ 완료 |

### 🟠 P1: Important (기능적 차이)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-007~~ | ~~External Uploads~~ | ~~S3/GCS 직접 업로드~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-008~~ | ~~Form Auto-Recovery~~ | ~~재연결 시 폼 상태 복구~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-009~~ | ~~live_session~~ | ~~인증/레이아웃 경계 관리~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-010~~ | ~~Page Title~~ | ~~동적 페이지 타이틀 변경~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-011~~ | ~~Flash Messages~~ | ~~일회성 알림 메시지~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-012~~ | ~~LongPolling Fallback~~ | ~~WebSocket 불가 시 폴백~~ | ~~중~~ | ⚪ 설계상 제외. WebSocket을 필수 전제로 둔다 ([설계 메모](./design/longpolling-fallback.md), [#59](https://github.com/itda-work/django-wireview/issues/59)) |
| ~~GAP-013~~ | ~~pushEvent (Hook→Server)~~ | ~~훅에서 서버로 이벤트 전송~~ | ~~중~~ | ✅ 완료 |

### 🟡 P2: Nice to Have (편의 기능)

| ID | 기능 | 설명 | 난이도 | 추적 |
|----|------|------|:------:|----------|
| ~~GAP-014~~ | ~~stream :limit~~ | ~~스트림 DOM 크기 제한~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-015~~ | ~~phx-viewport-*~~ | ~~양방향 무한 스크롤 바인딩~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-016~~ | ~~Image Preview~~ | ~~업로드 이미지 미리보기~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-017~~ | ~~start_async/cancel_async~~ | ~~세밀한 비동기 제어~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-018~~ | ~~phx-disabled-with~~ | ~~버튼 비활성화 텍스트~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-019~~ | ~~phx-feedback-for~~ | ~~폼 필드 에러 표시~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-020~~ | ~~enableProfiling~~ | ~~성능 프로파일링~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-021~~ | ~~on_mount hooks~~ | ~~공통 마운트 로직 모듈화~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-022~~ | ~~Telemetry~~ | ~~성능 측정 훅~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-023~~ | ~~onBeforeElUpdated~~ | ~~DOM 패치 전 콜백~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-025~~ | ~~Comprehensions~~ | ~~`{% for %}`를 항목 단위, `{% if %}`를 블록 단위 static/dynamic으로 분리해 항목·분기 변경 시 부분 diff~~ | ~~중~~ | ✅ 완료 (위치 기반, 키 기반은 미지원) |
| ~~GAP-026~~ | ~~Transport seam~~ | ~~`Outbound`/`Broker` 인터페이스 뒤로 채널 레이어 격리, 렌더 스냅샷 직렬화, wire-protocol 문서~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-028~~ | ~~Stream DOM 수명~~ | ~~재렌더가 `wire-stream` 컨테이너를 비우고, 같은 dom id 재삽입이 갱신이 아니라 중복이 된다~~ | ~~중~~ | ✅ 완료 |
| ~~GAP-029~~ | ~~세션 접근~~ | ~~컴포넌트가 Django 세션을 읽는다. 예제 둘이 없는 API를 상상해 쓰고 있었다~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-030~~ | ~~키 기반 comprehension~~ | ~~앞쪽 삽입이 뒤 항목 전부를 다시 보내지 않게~~ | ~~상~~ | ✅ 완료 (키 대신 내용으로 짝짓는다. `docs/design/keyed-comprehension.md`) |
| ~~GAP-031~~ | ~~내비게이션 테스트 헬퍼~~ | ~~`assert_patch`·`follow_redirect` 상당물~~ | ~~하~~ | ✅ 완료 |
| ~~GAP-032~~ | ~~Colocated hooks~~ | ~~컴포넌트 옆의 JS 훅을 자동 등록~~ | ~~중~~ | ✅ 완료 |
| GAP-033 | Sticky 컴포넌트 | boost 내비게이션을 건너 살아남는 컴포넌트 | 중 | [#72](https://github.com/itda-work/django-wireview/issues/72) |
| GAP-034 | Dead view | JS 없이도 읽히는 첫 렌더. 무엇을 약속할지부터 | 중 | [#73](https://github.com/itda-work/django-wireview/issues/73) |
| GAP-035 | LiveComponent 배치 업데이트 | 같은 컴포넌트 N개 갱신의 N+1 제거 | 중 | [#74](https://github.com/itda-work/django-wireview/issues/74) |
| ~~GAP-036~~ | ~~LiveComponent 슬롯~~ | ~~`{% live_component_block %}`으로 fill·기본 슬롯·let 전달~~ | ~~중~~ | ✅ 완료 |
| GAP-027 | Session extraction | 컨슈머 핸들러를 `WireviewSession`으로 분리, 세션 상태 export/import (docs/design/transport-abstraction.md) | 상 | [#60](https://github.com/itda-work/django-wireview/issues/60) 착수 기준 대기 |

---

## 4. 구현 로드맵

### Phase 1: JavaScript Interop ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: JavaScript Hooks & Interoperability  ✅ 완료      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-001: JavaScript Hooks ✅                               │
│  ├─ wire-hook="MyHook" 속성                                │
│  ├─ Hook 라이프사이클 (mounted, updated, destroyed, etc.)  │
│  ├─ Hook.pushEvent() → 서버 이벤트                         │
│  └─ handleEvent 클라이언트 핸들러                          │
│                                                             │
│  GAP-013: pushEvent ✅                                      │
│  └─ Hook에서 서버로 커스텀 이벤트 전송                     │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 2: Component System ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: Advanced Components                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-002: Slots ✅ 완료                                     │
│  ├─ {% fill "header" %}...{% endfill %}                    │
│  ├─ {% render_slot "header" %}                             │
│  ├─ default slot ({% render_slot %})                       │
│  └─ let: 바인딩 지원                                       │
│                                                             │
│  GAP-003: Function Components ✅ 완료                       │
│  ├─ @function_component 데코레이터                         │
│  ├─ {% func "name" %} 심플 태그                            │
│  ├─ {% func_block "name" %}...{% endfunc %} 블록 태그      │
│  └─ 타입 검증 및 슬롯 지원                                 │
│                                                             │
│  GAP-005: LiveComponent ✅ 완료                             │
│  ├─ {% live_component "Name" id="..." %} 태그              │
│  ├─ @myself 타겟팅 (myself=True)                           │
│  ├─ update() 콜백                                          │
│  └─ send_update() 부모→자식 통신                           │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 3: Navigation & Forms ✅ 완료

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: Navigation & Form Enhancement  ✅ 완료            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-004: handle_params ✅ 완료                             │
│  ├─ URL 변경 감지                                          │
│  └─ params_changed() 콜백                                  │
│                                                             │
│  GAP-008: Form Auto-Recovery ✅ 완료                        │
│  ├─ 재연결 시 폼 상태 저장/복구                            │
│  └─ wire-auto-recover 속성                                 │
│                                                             │
│  GAP-010: Page Title ✅ 완료                                │
│  └─ push_title() 메서드                                    │
│                                                             │
│  GAP-011: Flash Messages ✅ 완료                            │
│  └─ put_flash() / clear_flash()                            │
│                                                             │
│  구현 완료: 2025-12                                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Phase 4: Performance & Polish (진행 중)

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 4: Performance & Developer Experience                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GAP-006: temporary_assigns 🟡 #111                         │
│  └─ Meta.temporary_assigns                                 │
│                                                             │
│  GAP-007: External Uploads ✅ 완료                          │
│  └─ S3/GCS presigned URL 업로드                            │
│                                                             │
│  GAP-014-015: Stream 고급 기능 ✅ 완료                      │
│  ├─ :limit 옵션 ✅ 완료                                    │
│  └─ viewport 바인딩 ✅ 완료                                │
│                                                             │
│  GAP-020-022: Developer Tools ✅ 완료                       │
│  ├─ Type Stubs (.pyi) ✅ 완료                              │
│  ├─ LSP Metadata ✅ 완료                                   │
│  ├─ Profiling ✅ 완료                                      │
│  └─ Telemetry ✅ 완료                                      │
│                                                             │
│  예상 기간: 4-6주                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. GitHub Issue 구조

### Label 체계

```yaml
priority:
  - "priority: critical"   # P0
  - "priority: high"       # P1
  - "priority: medium"     # P2

type:
  - "type: feature"        # 새 기능
  - "type: enhancement"    # 기존 기능 개선
  - "type: dx"             # 개발자 경험

area:
  - "area: js-interop"     # JavaScript 연동
  - "area: components"     # 컴포넌트 시스템
  - "area: navigation"     # 네비게이션
  - "area: forms"          # 폼 처리
  - "area: uploads"        # 파일 업로드
  - "area: performance"    # 성능
  - "area: devtools"       # 개발자 도구

phase:
  - "phase: 1"
  - "phase: 2"
  - "phase: 3"
  - "phase: 4"
```

### Milestone 구조

```
v6.0.0-alpha.1 (Phase 1)
├── JavaScript Hooks
└── pushEvent

v6.0.0-alpha.2 (Phase 2)
├── Slots
├── Function Components
└── LiveComponent (optional)

v6.0.0-beta.1 (Phase 3)
├── handle_params ✅
├── Form Auto-Recovery
├── Page Title
└── Flash Messages

v6.0.0-rc.1 (Phase 4)
├── temporary_assigns
├── External Uploads
├── Stream Advanced
└── Developer Tools

v6.0.0 (Release)
└── Documentation & Polish
```

---

## 6. 구현 불가/제한 사항

### 런타임 한계

| 기능 | 이유 | 대안 |
|------|------|------|
| BEAM 수준 동시성 | Python GIL | asyncio + 수평 확장 |
| 프로세스 격리 | Python 스레드 모델 | 컴포넌트별 상태 격리 |
| 컴파일 타임 검증 | Python 동적 타이핑 | Pydantic + pyright |

### 프레임워크 차이

| Phoenix | Django | 접근 방식 |
|---------|--------|----------|
| HEEx 템플릿 | Django 템플릿 | 런타임 검증 |
| Ecto Changeset | Django Forms | Pydantic 검증 |
| OTP Supervisor | - | 재연결 로직 |

---

## 7. 참고 자료

- [Phoenix LiveView Documentation](https://hexdocs.pm/phoenix_live_view/)
- [Phoenix LiveView JavaScript Interop](https://hexdocs.pm/phoenix_live_view/js-interop.html)
- [Phoenix LiveComponent](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveComponent.html)
- [Phoenix Presence](https://hexdocs.pm/phoenix/Phoenix.Presence.html)

---

*이 문서는 Phoenix LiveView와의 기능 갭을 분석하고 구현 우선순위를 정의합니다.*
*최종 업데이트: 2025-06*
