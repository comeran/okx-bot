<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand">OKX Quant Bot</div>
      <el-menu router :default-active="$route.path" class="sidebar-menu">
        <el-menu-item index="/">Dashboard</el-menu-item>
        <el-menu-item index="/strategies">Strategies</el-menu-item>
        <el-menu-item index="/backtest">Backtest</el-menu-item>
        <el-menu-item index="/market">Market</el-menu-item>
        <el-menu-item index="/trades">Trades</el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <h1>Quant Trading Console</h1>
      </el-header>
      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #f5f7fa;
}

.sidebar {
  background: #ffffff;
  border-right: 1px solid #e4e7ed;
}

.brand {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}

.sidebar-menu {
  border-right: none;
}

.header {
  display: flex;
  align-items: center;
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
}

.header h1 {
  margin: 0;
  font-size: 20px;
  color: #303133;
}

.content {
  padding: 24px;
}
</style>
