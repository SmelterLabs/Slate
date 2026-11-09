<script lang="ts">
  // The task window's icons are the board's icon set (../Icon.svelte), a size larger by default.
  import Icon, { type IconName } from '../Icon.svelte';
  let { name, size = 16, fill = false }: { name: IconName; size?: number; fill?: boolean } = $props();
</script>

<Icon {name} {size} {fill} />
